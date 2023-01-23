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
import copy
import enum
import multiprocessing
import multiprocessing.connection
import os
import pathlib
import platform
import re
import selectors
import tempfile
import time
import typing
import unittest
import uuid

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
                 result_class=mpunittest.result.MergeableResult):
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

    def discover_and_run(
            self,
            start_dir: pathlib.Path,
            pattern: str = 'test*.py',
            top_level_dir: str = None,
            html_result_assets: HtmlResultAssets = None,
            time_unit: TimeUnit = TimeUnit.SECONDS
    ) -> typing.List[mpunittest.result.MergeableResult]:
        """
        Discover test cases in modules matching the given pattern in the given directory.

        :param start_dir: directory to search in
        :param pattern: pattern to check modules against
        :param top_level_dir: same as top_level_dir parameter of unittest.loader.discover
        :param html_result_assets: if given an HTML file containing the results will be generated
        according to the parameters in the given asset or with default parameters in case all are None,
        otherwise no files are generated
        :param time_unit: unit for displaying the time spent
        :return: list of test case results
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

        child_processes = list()
        process_conn_with_process_tuples: typing.List[typing.Tuple[
            multiprocessing.connection.Connection,
            multiprocessing.connection.Connection,
            multiprocessing.process.BaseProcess]
        ] = list()

        parent_conn_to_process_mapping: typing.Dict[
            multiprocessing.connection.Connection,
            multiprocessing.process.BaseProcess
        ] = dict()

        for _ in range(self._process_count):
            respective_parent_conn, child_conn = self._mp_context.Pipe(duplex=True)

            child_process = self._mp_context.Process(daemon=self._daemons,
                                                     target=self.process_target,
                                                     args=(child_conn,
                                                           start_dir,
                                                           pattern,
                                                           top_level_dir,
                                                           self._result_class,
                                                           result_path,))

            self._logger.debug('will start process with name "%s"', child_process.name)
            child_process.start()
            self._logger.info('started process with name "%s"', child_process.name)

            process_conn_with_process_tuples.append((respective_parent_conn, child_conn, child_process))
            parent_conn_to_process_mapping[respective_parent_conn] = child_process
            child_processes.append(child_process)

        assert set(child_processes) == \
               set(parent_conn_to_process_mapping.values()) == \
               set((p for _, __, p in process_conn_with_process_tuples))

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

        if platform.system() != 'Windows':
            read_selector = selectors.DefaultSelector()
            for respective_parent_conn, child_conn, _ in process_conn_with_process_tuples:
                read_selector.register(respective_parent_conn, selectors.EVENT_READ)

        for conn, _, process in process_conn_with_process_tuples:
            try:
                test_id = test_ids.pop()
                # TODO: check whether it is writeable
                conn.send(test_id)  # TODO: consider doing this in while True loop below
                self._logger.info('primary runner process delegated run of %s to %s',
                                  test_id, process.name)
            except IndexError:
                break

        next_to_send = list()

        if platform.system() != 'Windows':
            write_selectors = dict()
            for respective_parent_conn, child_conn, _ in process_conn_with_process_tuples:
                assert respective_parent_conn.fileno() not in write_selectors
                specific_write_selector = selectors.DefaultSelector()
                specific_write_selector.register(respective_parent_conn, selectors.EVENT_WRITE)
                write_selectors[respective_parent_conn.fileno()] = specific_write_selector

        test_results = list()

        assert child_processes
        loop_counter = 0
        while True:
            if loop_counter == 0:
                if not all(map(multiprocessing.Process.is_alive, child_processes)):
                    raise RuntimeError('At least one process terminated prematurely.')

            loop_counter += 1
            loop_counter %= 100

            if platform.system() != 'Windows':
                read_events = read_selector.select(timeout=0.001)
                for key, mask in read_events:
                    respective_parent_conn = key.fileobj
                    test_result = respective_parent_conn.recv()
                    test_results.append(test_result)
                    self._logger.info('process with the name "%s" finished a '
                                      'run with the following result: "%s"',
                                      parent_conn_to_process_mapping[respective_parent_conn].name,
                                      test_result.test_id_to_result_mapping)

                    next_to_send.append(respective_parent_conn)  # TODO: only do this when recv was successful
            else:
                for respective_parent_conn in multiprocessing.connection.wait(
                        [c for c, _, __ in process_conn_with_process_tuples], timeout=0.001):
                    test_result = respective_parent_conn.recv()
                    test_results.append(test_result)
                    self._logger.info('process with the name "%s" finished a '
                                      'run with the following result: "%s"',
                                      parent_conn_to_process_mapping[respective_parent_conn].name,
                                      test_result.test_id_to_result_mapping)

                    next_to_send.append(respective_parent_conn)  # TODO: only do this when recv was successful

            if not test_ids:
                # TODO: compare sets of fileno instead
                if len(next_to_send) == min(test_id_count, len(process_conn_with_process_tuples)):
                    break

                continue

            for conn in copy.copy(next_to_send):
                if not test_ids:
                    break

                if platform.system() != 'Windows':
                    write_events = write_selectors[conn.fileno()].select(timeout=0.0001)
                    for key, mask in write_events:
                        respective_parent_conn = key.fileobj

                        try:
                            test_id = test_ids.pop()
                            respective_parent_conn.send(test_id)
                            self._logger.info('primary runner process delegated run of %s to %s',
                                              test_id,
                                              parent_conn_to_process_mapping[conn].name)
                        except IndexError:
                            break
                        next_to_send.remove(conn)
                else:
                    try:
                        test_id = test_ids.pop()
                        conn.send(test_id)
                        self._logger.info('primary runner process delegated run of %s to %s',
                                          test_id,
                                          parent_conn_to_process_mapping[conn].name)
                    except IndexError:
                        continue
                    next_to_send.remove(conn)

        # cleanup starts here
        for respective_parent_conn, child_conn, child_process in process_conn_with_process_tuples:
            self._logger.debug('will send termination signal to process with name "%s"',
                               child_process.name)
            respective_parent_conn.send(-1)
            self._logger.info('send termination signal to process with name "%s"',
                              child_process.name)
            answer_for_shutdown_request = respective_parent_conn.recv()  # TODO: implement values other than -2
            assert answer_for_shutdown_request == -2
            self._logger.info('received termination signal from process with name "%s"',
                              child_process.name)
            respective_parent_conn.close()

        self._logger.debug('will wait for child processes to finish')
        assert child_processes
        for child_process in child_processes:
            child_process.join()
            exit_code = child_process.exitcode
            if not exit_code == 0:
                self._logger.error('process with name "%s" exited with code "%i"',
                                   child_process.name,
                                   exit_code)
                raise RuntimeError(f'Expected exitcode of {child_process.name} to be 0, '
                                   f'but it is {exit_code} instead.')

        end = time.monotonic_ns()
        total_time_spent_ns = end - start  # TODO: maybe add up the subprocess times instead

        if html_result_assets:
            self._logger.debug('will generate html file in %s', result_path)
            self._generate_html(test_results=test_results,
                                result_path=result_path,
                                total_time_spent_ns=total_time_spent_ns,
                                doc_title=doc_title,
                                html_file_name=html_file_name,
                                time_unit=time_unit)
            self._logger.info('generated html file in %s', result_path)

        return test_results

    @staticmethod
    def _generate_html(test_results: typing.List[mpunittest.result.MergeableResult],
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
        for test_result in test_results:
            assert test_result.log_file.is_file()

            for test_id, extra_class in test_result.test_id_to_result_mapping.items():
                table_row_data.append(
                    (test_id,
                     test_result.time_spent_per_test_id[test_id],
                     test_result.log_file.name,
                     extra_class)
                )
        table_row_data = [(i, *d) for i, d in enumerate(table_row_data)]

        table_rows_string = str()

        pass_count = 0
        fail_count = 0
        skip_count = 0

        for index, test_id, time_spent, log_file_name, extra_class in table_row_data:
            table_rows_string += _tr_template.format(
                html_id_string=str(index),
                test_id=test_id,
                duration=ns_to_time_unit(time_spent=time_spent, time_unit=time_unit),
                log_file=log_file_name,
                extra_class=extra_class,
                text=extra_class.upper()
            )

            if extra_class == mpunittest.result.MergeableResult.Result.PASS:
                pass_count += 1
            elif extra_class == mpunittest.result.MergeableResult.Result.FAIL:
                fail_count += 1
            elif extra_class == mpunittest.result.MergeableResult.Result.SKIPPED:
                skip_count += 1
            else:
                # TODO: also call logger here
                raise ValueError(f'Unexpected value for extra_class: '
                                 f'"{extra_class}" of type "{type(extra_class)}"')

        total_count = sum((pass_count, fail_count, skip_count))

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
            child_conn: multiprocessing.connection.Connection,
            start_dir: pathlib.Path,
            pattern: str,
            top_level_dir: str,
            result_class: typing.Type[mpunittest.result.MergeableResult],
            result_path: pathlib.Path
    ) -> None:
        """
        Run inside of child processes and execute test cases with the ids that are send to the
        respective child process.
        Also send back the result of the execution and optionally write stderr and stdout in a file
        together with the result.

        :param child_conn: connection to receive test ids on
        :param start_dir: directory to search for test cases to eventually load them and execute them
        if requested
        :param pattern: pattern to check modules against
        :param top_level_dir: same as top_level_dir parameter of unittest.loader.discover
        :param result_class: type to instantiate for saving unittest results
        :param result_path: path to generate result files in, can be None to not generate any files
        :return: None
        """
        try:
            id_to_test_mapping = MergingRunner._discover_id_to_test_mapping(start_dir, pattern, top_level_dir)

            # TODO: consider using the time for the dir instead of file names
            time_str = str(time.time()).replace('.', '_')
            filename_postfix = f'pid{os.getpid()}_t{time_str}'
            stderr_filename = 'stderr' + filename_postfix
            stdout_filename = 'stdout' + filename_postfix

            while True:
                # TODO:
                #  consider doing read above and directly passing
                #  it into a function that evaluates the test case

                test_id = child_conn.recv()
                if test_id == -1:
                    break

                if result_path:
                    log_file_name = result_path.joinpath('test' + uuid.uuid4().hex + '.log')

                    temp_dir_ctx = tempfile.TemporaryDirectory
                    stderr_redirect_ctx = mpunittest.streamctx.redirect_stderr_to_file
                    stdout_redirect_ctx = mpunittest.streamctx.redirect_stdout_to_file

                else:
                    log_file_name = None

                    temp_dir_ctx = MergingRunner.dummy_context
                    stderr_redirect_ctx = MergingRunner.dummy_context
                    stdout_redirect_ctx = MergingRunner.dummy_context

                result = result_class(log_file_path=log_file_name)

                with temp_dir_ctx() as temp_dir:

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
                        # execute test here
                        id_to_test_mapping[test_id](result)

                    if log_file_name:
                        with \
                                open(log_file_name, 'wb') as merged, \
                                open(final_stderr_filename, 'rb') as stderr_file, \
                                open(final_stdout_filename, 'rb') as stdout_file:
                            if len(result.test_id_to_result_mapping) == 1:
                                id_to_write = list(result.test_id_to_result_mapping.keys())[0]
                                merged.write(f'Log for test with id {id_to_write} '
                                             f'run at {time_str}: \n\n'.encode('utf8'))
                            else:
                                ids_to_write = set(result.test_id_to_result_mapping.keys())
                                merged.write(f'Log for tests with ids {ids_to_write} '
                                             f'run at {time_str}: \n\n'.encode('utf8'))
                            merged.write(('#' * 10 + ' ' * 2 + 'stderr' + ' ' * 2 + '#' * 10 + '\n\n').encode('utf8'))
                            MergingRunner.copy_content_with_limited_buffer(stderr_file, merged)
                            merged.write(('\n' + '#' * 30 + '\n\n').encode('utf8'))
                            merged.write(('#' * 10 + ' ' * 2 + 'stdout' + ' ' * 2 + '#' * 10 + '\n\n').encode('utf8'))
                            MergingRunner.copy_content_with_limited_buffer(stdout_file, merged)
                            merged.write(('\n' + '#' * 30 + '\n\n').encode('utf8'))
                            merged.write(f'Overall result: {result.overall_result()}\n'.encode('utf8'))

                child_conn.send(result)

            child_conn.send(-2)
        finally:
            child_conn.close()

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
