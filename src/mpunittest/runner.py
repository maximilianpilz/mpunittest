"""
This file is part of mpunittest, a parallel unittest runner.
Copyright (C) 2023 Maximilian Pilz

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; version 2.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.
"""
import collections
import contextlib
import dataclasses
import enum
import multiprocessing
import multiprocessing.connection
import multiprocessing.shared_memory
import os
import pathlib
import re
import tempfile
import time
import typing
import unittest
import uuid

import mpunittest.comm
import mpunittest.html
import mpunittest.logging
import mpunittest.result
import mpunittest.streamctx

_tr_template = \
    """
<tr id="{html_id_string}">
    <td>{test_id}</td>
    <td>{duration}</td>
    <td>
        <a href="{log_file}" class="button {extra_class}"><span>{text}</span></a>
    </td>
</tr>
    """

HtmlResultAssets = collections.namedtuple('HtmlResultAssets', ('document_title', 'document_file_name', 'result_path'))


class TempDirOrDummyContextManagerFactory(typing.Protocol):
    def __call__(self) -> typing.ContextManager[str]: ...


@dataclasses.dataclass
class LogFileParameters:
    log_file_names: typing.List[typing.Optional[pathlib.Path]]

    temp_dir_ctx: TempDirOrDummyContextManagerFactory
    stderr_redirect_ctx: mpunittest.streamctx.RedirectionContextManagerFactory
    stdout_redirect_ctx: mpunittest.streamctx.RedirectionContextManagerFactory


class TimeUnit(int, enum.Enum):
    """
    Time units given in nanoseconds.
    """
    NANOSECONDS = 0
    MICROSECONDS = 3
    MILLISECONDS = 6
    SECONDS = 9


def ns_to_time_unit(time_spent: int, time_unit: TimeUnit) -> str:
    time_digits = list()
    time_digits.append(time_spent // (10 ** time_unit))
    next_digits = time_spent % (10 ** time_unit)
    if next_digits:
        time_digits.append('0' * (time_unit - len(str(next_digits))) + str(next_digits).rstrip('0'))
    return '.'.join(map(str, time_digits)) + ' ' + time_unit.name.lower()


class IPCMethod(enum.Enum):
    SHARED_MEMORY = 0
    PIPE_BASED_QUEUE = 1


class MergingRunner:
    """
    An unittest runner for parallel unittest execution that (optionally) can merge results into a file.
    """
    _logger = mpunittest.logging.logger.getChild('runner')

    _regex = re.compile('(?s)<!--.*-->\n')

    def __init__(self,
                 process_count: int = 2,
                 mp_context: multiprocessing.context.BaseContext = None,
                 daemons: bool = True,
                 result_class=mpunittest.result.SubProcessResult,
                 ipc_method: IPCMethod = IPCMethod.SHARED_MEMORY):
        """
        :param process_count: amount of process to start for delegating unittest execution to them
        :param mp_context: multiprocessing context to use for starting the processes e.g. the 'spawn' context
        :param daemons: will be used as daemon flag for process creation
        :param result_class: type to instantiate for saving unittest results
        """
        assert process_count > 0
        self._process_count = process_count

        if mp_context is None:
            self._mp_context: multiprocessing.context.BaseContext = multiprocessing.get_context('spawn')
        else:
            self._mp_context: multiprocessing.context.BaseContext = mp_context

        self._result_class = result_class
        self._daemons = daemons

        self._ipc_method = ipc_method

        if self._ipc_method == IPCMethod.SHARED_MEMORY:
            self._logger.warning(f'{self.__class__.__name__} instance is configured to use '
                                 f'the {IPCMethod.__name__} "{self._ipc_method}", which is an '
                                 f'experimental feature, there may be memory leaks outside of '
                                 f'the memory space of processes. If you do not want this, '
                                 f'you can specify another {IPCMethod.__name__} such as '
                                 f'{IPCMethod.PIPE_BASED_QUEUE} to be used')

    @staticmethod
    def flatten(test_obj: typing.Union[unittest.TestSuite, unittest.TestCase]) -> typing.Generator:
        """
        Generate individual test case values out of test suites and do an identity mapping in case of a test case.

        :param test_obj: test suite or test case, for test suites all test cases will be extracted from it
        :return: a generator for test cases
        """
        try:
            for i in test_obj:
                for e in MergingRunner.flatten(i):
                    yield e
        except TypeError:
            yield test_obj

    @staticmethod
    def _discover_ids(*args, **kwargs) -> list:

        loader = unittest.loader.TestLoader()
        test_generator = MergingRunner.flatten(loader.discover(*args, **kwargs))

        return [test.id() for test in test_generator]

    @staticmethod
    def _discover_id_to_test_mapping(*args, **kwargs) -> dict:

        loader = unittest.loader.TestLoader()
        test_generator = MergingRunner.flatten(loader.discover(*args, **kwargs))

        return {test.id(): test for test in test_generator}

    @staticmethod
    def _get_log_file_parameters(result_path, result_file_count: int):

        if result_path:
            log_file_names = [result_path.joinpath('test' + uuid.uuid4().hex + '.log')
                              for _ in range(result_file_count)]
            assert len(set(log_file_names)) == len(log_file_names)

            temp_dir_ctx = tempfile.TemporaryDirectory
            stderr_redirect_ctx = mpunittest.streamctx.redirect_stderr_to_file
            stdout_redirect_ctx = mpunittest.streamctx.redirect_stdout_to_file

        else:
            log_file_names = [None] * result_file_count

            temp_dir_ctx = MergingRunner.dummy_context
            stderr_redirect_ctx = MergingRunner.dummy_context
            stdout_redirect_ctx = MergingRunner.dummy_context

        return LogFileParameters(log_file_names=log_file_names,
                                 temp_dir_ctx=temp_dir_ctx,
                                 stderr_redirect_ctx=stderr_redirect_ctx,
                                 stdout_redirect_ctx=stdout_redirect_ctx)

    def discover_and_run(
            self,
            start_dir: pathlib.Path,
            pattern: str = 'test*.py',
            top_level_dir: str = None,
            html_result_assets: HtmlResultAssets = None,
            time_unit: TimeUnit = TimeUnit.SECONDS
    ) -> typing.List[typing.Tuple[str, mpunittest.result.SimpleResult, int, pathlib.Path]]:
        """
        Discover test cases in modules matching the given pattern in the given directory.

        :param start_dir: directory to search in
        :param pattern: pattern to check modules against
        :param top_level_dir: same as top_level_dir parameter of unittest.loader.discover
        :param html_result_assets: if given an HTML file containing the results will be generated
        according to the parameters in the given asset or with default parameters in case all are None,
        otherwise no files are generated
        :param time_unit: unit for displaying the time spent
        :return: TODO
        """
        start = time.monotonic_ns()

        if html_result_assets:
            if html_result_assets.result_path:
                result_path = html_result_assets.result_path
            else:
                result_path = pathlib.Path(f'testruns') / pathlib.Path(f'testrun{uuid.uuid4().hex}')
            result_path.mkdir(parents=True, exist_ok=True)

            if html_result_assets.document_title:
                doc_title = html_result_assets.document_title
            else:
                doc_title = 'Unittest results'

            if html_result_assets.document_file_name:
                html_file_name = html_result_assets.document_file_name
            else:
                html_file_name = 'test_result'
        else:
            result_path = None
            doc_title = None
            html_file_name = None

        self._logger.debug('primary runner process will start discovery in directory "%s" '
                           'with pattern "%s" and top level directory "%s"',
                           start_dir, pattern, top_level_dir)
        test_ids = MergingRunner._discover_ids(start_dir=start_dir,
                                               pattern=pattern,
                                               top_level_dir=top_level_dir)
        self._logger.info('primary runner process finished discovery')
        test_id_count = len(test_ids)
        self._logger.info('primary runner process discovered %i test(s) in "%s"',
                          test_id_count,
                          start_dir.as_uri())

        log_file_parameters: LogFileParameters = self._get_log_file_parameters(
            result_path=result_path,
            result_file_count=test_id_count
        )

        if self._ipc_method == IPCMethod.SHARED_MEMORY:  # TODO: change type of _ipc_method to call it here
            communicator = mpunittest.comm.SharedMemoryCommunicator(
                code_count=test_id_count, mp_context=self._mp_context
            )
        elif self._ipc_method == IPCMethod.PIPE_BASED_QUEUE:
            communicator = mpunittest.comm.PipeCommunicator(
                code_count=test_id_count, mp_context=self._mp_context)
        else:
            raise NotImplementedError

        try:
            child_processes = list()

            # TODO: consider using test_ids without enumeration
            enumerated_test_ids = tuple(enumerate(test_ids))  # TODO: evaluate whether this is really necessary

            for _ in range(self._process_count):
                child_process = self._mp_context.Process(daemon=self._daemons,
                                                         target=self.process_target,
                                                         args=(enumerated_test_ids,
                                                               communicator,
                                                               start_dir,
                                                               pattern,
                                                               top_level_dir,
                                                               self._result_class,
                                                               log_file_parameters
                                                               ,))

                self._logger.debug('will start process with name "%s"', child_process.name)
                child_process.start()
                self._logger.info('started process with name "%s"', child_process.name)

                child_processes.append(child_process)

            assert len(child_processes) == self._process_count

            self._logger.debug('will wait for child processes to finish')
            assert child_processes
            for child_process in child_processes:
                child_process.join()
                exit_code = child_process.exitcode

                # since no timeout was provided exit_code is not allowed to be None
                assert exit_code is not None, 'exitcode was None even though no timeout was provided to join'

                if not exit_code == 0:
                    self._logger.error('process with name "%s" exited with code "%i"',
                                       child_process.name,
                                       exit_code)
                    raise RuntimeError(f'Expected exitcode of {child_process.name} to be 0, '
                                       f'but it is {exit_code} instead.')

            if not communicator.is_finished():
                raise RuntimeError('All subprocesses have been joined, but not all results are known.')

            test_results = communicator.get_results()
        finally:
            communicator.close()
            communicator.unlink()

        end = time.monotonic_ns()
        total_time_spent_ns = end - start  # TODO: maybe add up the subprocess times instead

        assert len(test_results) == test_id_count

        converted_results = [(test_ids[result_index],
                              mpunittest.result.TransmissionCodeToSimpleResult[result_transmission_code],
                              result_duration,
                              log_file_parameters.log_file_names[result_index])
                             for result_index, result_transmission_code, result_duration in test_results]

        assert all(c in mpunittest.result.SimpleResult for _, c, __, ___ in converted_results)

        if html_result_assets:
            self._logger.debug('will generate html file in %s', result_path)
            self._generate_html(test_results=converted_results,
                                result_path=result_path,
                                total_time_spent_ns=total_time_spent_ns,
                                doc_title=doc_title,
                                html_file_name=html_file_name,
                                time_unit=time_unit)
            self._logger.info('generated html file in %s', result_path)

        return converted_results

    @staticmethod
    def _generate_html(test_results: typing.List[typing.Tuple[str, mpunittest.result.SimpleResult, int, pathlib.Path]],
                       result_path: pathlib.Path,
                       total_time_spent_ns: int,
                       doc_title: str,
                       html_file_name: str,
                       time_unit: TimeUnit):
        with open(
                pathlib.Path(mpunittest.html.__file__).parent.joinpath('result.html'), 'r'
        ) as html_template:
            template_data = html_template.read()

        matches = MergingRunner._regex.findall(template_data)
        assert matches
        assert len(matches) == 1
        for match in matches:
            template_data = template_data.replace(match, '')

        table_row_data = list()
        for test_id, simple_result, duration, log_file in test_results:
            assert log_file.is_file()

            table_row_data.append(
                (test_id,
                 duration,
                 log_file.name,
                 simple_result)
            )
        table_row_data = [(i, *d) for i, d in enumerate(table_row_data)]

        table_rows_string = str()

        pass_count = 0
        fail_count = 0
        skip_count = 0
        error_count = 0

        for index, test_id, time_spent, log_file_name, extra_class in table_row_data:
            table_rows_string += _tr_template.format(
                html_id_string=str(index),
                test_id=test_id,
                duration=ns_to_time_unit(time_spent=time_spent, time_unit=time_unit),
                log_file=log_file_name,
                extra_class=extra_class.name.lower(),
                text=extra_class.name.upper()
            )

            if extra_class == mpunittest.result.SimpleResult.PASS:
                pass_count += 1
            elif extra_class == mpunittest.result.SimpleResult.FAIL:
                fail_count += 1
            elif extra_class == mpunittest.result.SimpleResult.SKIPPED:
                skip_count += 1
            elif extra_class == mpunittest.result.SimpleResult.ERROR:
                error_count += 1
            else:
                # TODO: also call logger here
                raise ValueError(f'Unexpected value for extra_class: '
                                 f'"{extra_class}" of type "{type(extra_class)}"')

        total_count = sum((pass_count, fail_count, skip_count, error_count))

        original_order = [e[0] for e in table_row_data]

        # sort by test_id
        sorted_by_name_asc = [e[0] for e in sorted(
            table_row_data, key=lambda e: e[1])]
        sorted_by_name_desc = [e[0] for e in sorted(
            table_row_data, key=lambda e: e[1], reverse=True)]

        # sort by time_spent
        sorted_by_time_asc = [e[0] for e in sorted(
            table_row_data, key=lambda e: e[2])]
        sorted_by_time_desc = [e[0] for e in sorted(
            table_row_data, key=lambda e: e[2], reverse=True)]

        with open(result_path.joinpath(f'{html_file_name}.html'), 'w') as final_html_file:
            final_html_data = template_data.format(
                title=doc_title,
                table_title=doc_title,
                time=f'{ns_to_time_unit(time_spent=total_time_spent_ns, time_unit=time_unit)}',
                total_count=total_count,
                pass_count=pass_count,
                fail_count=fail_count,
                skip_count=skip_count,
                error_count=error_count,
                table_rows=table_rows_string,
                ordered_by_name=','.join(
                    map(str,
                        [
                            original_order,
                            sorted_by_name_asc,
                            sorted_by_name_desc
                        ])),
                ordered_by_time=','.join(
                    map(str,
                        [
                            original_order,
                            sorted_by_time_asc,
                            sorted_by_time_desc
                        ]))
            )

            final_html_file.write(final_html_data)

    @staticmethod
    def process_target(
            enumerated_test_ids,
            communicator,
            start_dir: pathlib.Path,
            pattern: str,
            top_level_dir: str,
            result_class: typing.Type[mpunittest.result.SubProcessResult],
            log_file_parameters: LogFileParameters
    ) -> None:
        """
        Run inside of child processes and execute test cases with the ids that are send to the
        respective child process.
        Also send back the result of the execution and optionally write stderr and stdout in a file
        together with the result.

        :param enumerated_test_ids: TODO
        :param start_dir: directory to search for test cases to eventually load them and execute them
        if requested
        :param pattern: pattern to check modules against
        :param top_level_dir: same as top_level_dir parameter of unittest.loader.discover
        :param result_class: type to instantiate for saving unittest results
        :param result_path: path to generate result files in, can be None to not generate any files
        :return: None
        """
        try:
            index_to_test_id = {i: e for i, e in enumerated_test_ids}

            temp_dir_ctx = log_file_parameters.temp_dir_ctx
            stderr_redirect_ctx = log_file_parameters.stderr_redirect_ctx
            stdout_redirect_ctx = log_file_parameters.stdout_redirect_ctx

            id_to_test_mapping = MergingRunner._discover_id_to_test_mapping(start_dir, pattern, top_level_dir)

            time_str = str(time.time()).replace('.', '_')  # TODO: remove when no longer needed
            filename_postfix = f'pid{os.getpid()}_t{time_str}'
            stderr_filename = 'stderr' + filename_postfix
            stdout_filename = 'stdout' + filename_postfix

            while True:

                test_id_index = communicator.get()
                if test_id_index is None:
                    break
                test_id = index_to_test_id[test_id_index]
                log_file_name = log_file_parameters.log_file_names[test_id_index]

                result = result_class()  # TODO: add parameters here such as stream and verbosity

                with temp_dir_ctx() as temp_dir:  # TODO: move out of loop

                    if temp_dir:
                        temp_path = pathlib.Path(temp_dir).resolve()
                        final_stderr_filename = temp_path.joinpath(stderr_filename)
                        final_stdout_filename = temp_path.joinpath(stdout_filename)
                    else:
                        final_stderr_filename = None
                        final_stdout_filename = None

                    with \
                            stderr_redirect_ctx(final_stderr_filename), \
                            stdout_redirect_ctx(final_stdout_filename):

                        result.startTestRun()
                        # execute test here
                        id_to_test_mapping[test_id](result)
                        result.stopTestRun()

                    transmission_code, str_output = result.overall_result

                    if log_file_name:
                        with \
                                open(log_file_name, 'wb') as merged, \
                                open(final_stderr_filename, 'rb') as stderr_file, \
                                open(final_stdout_filename, 'rb') as stdout_file:
                            merged.write(f'Log for "{test_id}" '
                                         f'run at {time_str}: \n\n'.encode('utf8'))

                            merged.write(('#' * 10 + ' ' * 2 + 'stderr' + ' ' * 2 + '#' * 10 + '\n\n').encode('utf8'))
                            MergingRunner.copy_content_with_limited_buffer(stderr_file, merged)
                            merged.write(('\n' + '#' * 30 + '\n\n').encode('utf8'))
                            merged.write(('#' * 10 + ' ' * 2 + 'stdout' + ' ' * 2 + '#' * 10 + '\n\n').encode('utf8'))
                            MergingRunner.copy_content_with_limited_buffer(stdout_file, merged)
                            merged.write(('\n' + '#' * 30 + '\n\n').encode('utf8'))
                            merged.write(f'Overall result: {transmission_code.name}\n'.encode('utf8'))
                            merged.write(f'\n{str_output}\n'.encode('utf8'))

                communicator.put(index=test_id_index,
                                 transmission_code=transmission_code,
                                 duration=result.time_spent)
        finally:
            communicator.close()

    @staticmethod
    def copy_content_with_limited_buffer(
            src: typing.IO,
            dst: typing.IO,
            buffer_size: int = 1000
    ) -> None:
        """
        Copy data from src to dst.

        :param src: io object to read from
        :param dst: io object to write to i.e. to copy to
        :param buffer_size: maximum amount of unit defined by the given io objects to read per iteration
        :return: None
        """
        while True:
            data = src.read(buffer_size)
            if not data:
                break
            dst.write(data)

    @staticmethod
    @contextlib.contextmanager
    def dummy_context(*_, **__) -> typing.Generator:
        """
        Do not do anything additionally to the code in the context.
        This is supposed to be a syntactical replacement for other contexts in case that the other contexts shall not
        be used.

        :return: generator that can be converted into context manager that does effectively nothing
        """
        yield
