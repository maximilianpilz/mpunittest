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
import mpunittest.result
import mpunittest.streamctx


_tr_template = \
    """
<tr>
    <td>{test_id}</td>
    <td>{duration}</td>
    <td>
        <a href="{log_file}" class="button {extra_class}"><span>{text}</span></a>
    </td>
</tr>
    """

HtmlResultAssets = collections.namedtuple('HtmlResultAssets', ('document_title', 'document_file_name', 'result_path'))


class MergingRunner:
    _regex = re.compile('(?s)<!--.*-->\n')

    def __init__(self,
                 process_count: int = 2,
                 mp_context: multiprocessing.context.BaseContext = None,
                 daemons: bool = True,
                 result_class=mpunittest.result.MergeableResult):
        self._process_count = process_count

        if mp_context is None:
            self._mp_context: multiprocessing.context.BaseContext = multiprocessing.get_context('spawn')
        else:
            self._mp_context: multiprocessing.context.BaseContext = mp_context

        self._result_class = result_class
        self._daemons = daemons

    @staticmethod
    def flatten(test_obj: typing.Union[unittest.TestSuite, unittest.TestCase]):
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
            # doc_title: str = 'Unittest results',
            # html_file_name: str = 'test_result',
            # result_path: pathlib.Path = None
    ) -> typing.List[mpunittest.result.MergeableResult]:
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
        process_conn_tuples: typing.List[typing.Tuple[
            multiprocessing.connection.Connection,
            multiprocessing.connection.Connection]
        ] = list()

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
            child_processes.append(child_process)
            child_process.start()

            process_conn_tuples.append((respective_parent_conn, child_conn))

        test_ids = MergingRunner._discover_ids(start_dir=start_dir,
                                               pattern=pattern,
                                               top_level_dir=top_level_dir)
        test_id_count = len(test_ids)

        if platform.system() != 'Windows':
            read_selector = selectors.DefaultSelector()
            for respective_parent_conn, child_conn in process_conn_tuples:
                read_selector.register(respective_parent_conn, selectors.EVENT_READ)

        for conn, _ in process_conn_tuples:
            try:
                conn.send(test_ids.pop())  # TODO: consider doing this in while True loop below
            except IndexError:
                break

        next_to_send = list()

        if platform.system() != 'Windows':
            write_selectors = dict()
            for respective_parent_conn, child_conn in process_conn_tuples:
                assert respective_parent_conn.fileno() not in write_selectors
                specific_write_selector = selectors.DefaultSelector()
                specific_write_selector.register(respective_parent_conn, selectors.EVENT_WRITE)
                write_selectors[respective_parent_conn.fileno()] = specific_write_selector

        test_results = list()

        while True:
            if platform.system() != 'Windows':
                read_events = read_selector.select(timeout=0.001)
                for key, mask in read_events:
                    respective_parent_conn = key.fileobj
                    test_results.append(respective_parent_conn.recv())

                    next_to_send.append(respective_parent_conn)  # TODO: only do this when recv was successful
            else:
                for respective_parent_conn in multiprocessing.connection.wait(
                        [c for c, _ in process_conn_tuples], timeout=0.001):
                    test_results.append(respective_parent_conn.recv())

                    next_to_send.append(respective_parent_conn)  # TODO: only do this when recv was successful

            if not test_ids:
                # TODO: compare sets of fileno instead
                if len(next_to_send) == min(test_id_count, len(process_conn_tuples)):
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
                            respective_parent_conn.send(test_ids.pop())
                        except IndexError:
                            break
                        next_to_send.remove(conn)
                else:
                    try:
                        conn.send(test_ids.pop())
                    except IndexError:
                        continue
                    next_to_send.remove(conn)

        # cleanup starts here
        for respective_parent_conn, child_conn in process_conn_tuples:
            respective_parent_conn.send(-1)
            answer_for_shutdown_request = respective_parent_conn.recv()  # TODO: implement values other than -2
            assert answer_for_shutdown_request == -2
            respective_parent_conn.close()

        for child_process in child_processes:
            child_process.join()

        end = time.monotonic_ns()
        total_time_spent_ns = end - start

        if html_result_assets:
            self._generate_html(test_results=test_results,
                                result_path=result_path,
                                total_time_spent_ns=total_time_spent_ns,
                                doc_title=doc_title,
                                html_file_name=html_file_name)

        return test_results

    @staticmethod
    def _generate_html(test_results: typing.List[mpunittest.result.MergeableResult],
                       result_path: pathlib.Path,
                       total_time_spent_ns: int,
                       doc_title: str,
                       html_file_name: str):
        with open(
                pathlib.Path(mpunittest.html.__file__).parent.joinpath('result.html'), 'r'
        ) as html_template:
            template_data = html_template.read()

        matches = MergingRunner._regex.findall(template_data)
        assert matches
        assert len(matches) == 1
        for match in matches:
            template_data = template_data.replace(match, '')

        table_data = str()
        for test_result in test_results:
            assert test_result.log_file.is_file()

            for test_id, extra_class in test_result.test_id_to_result_mapping.items():
                table_data += _tr_template.format(
                    test_id=test_id,
                    duration=f'{test_result.time_spent_per_test_id[test_id]}ns',
                    log_file=test_result.log_file.name,
                    extra_class=extra_class,
                    text=extra_class.upper()
                )

        with open(result_path.joinpath(f'{html_file_name}.html'), 'w') as final_html_file:
            final_html_data = template_data.replace('{table_rows}', table_data)
            final_html_data = final_html_data.replace('{time}', f'{total_time_spent_ns}ns')
            final_html_data = final_html_data.replace('{title}', doc_title)

            final_html_file.write(final_html_data)

    @staticmethod
    def process_target(
            child_conn: multiprocessing.connection.Connection,
            start_dir: pathlib.Path,
            pattern: str,
            top_level_dir: str,
            result_class: typing.Type[mpunittest.result.MergeableResult],
            result_path: pathlib.Path
    ):
        try:
            id_to_test_mapping = MergingRunner._discover_id_to_test_mapping(start_dir, pattern, top_level_dir)

            time_str = str(time.time()).replace('.', '_')  # TODO: consider using the time for the dir instead of file names
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
                                merged.write(f'Log for test with id {id_to_write} run at {time_str}: \n\n'.encode('utf8'))
                            else:
                                ids_to_write = set(result.test_id_to_result_mapping.keys())
                                merged.write(f'Log for tests with ids {ids_to_write} run at {time_str}: \n\n'.encode('utf8'))
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
    ):
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
