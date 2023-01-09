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

import contextlib
import copy
import multiprocessing
import os
import pathlib
import selectors
import tempfile
import time
import typing
import unittest
import uuid
import re

import mpunittest.result
import mpunittest.html

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


class MergingRunner:
    _regex = re.compile('(?s)<!--.*-->\n')

    def __init__(self,
                 process_count: int = 2,
                 mp_context: multiprocessing.context.BaseContext = None,
                 result_class=mpunittest.result.MergeableResult):
        self._process_count = process_count

        if mp_context is None:
            self._mp_context: multiprocessing.context.BaseContext = multiprocessing.get_context('spawn')
        else:
            self._mp_context: multiprocessing.context.BaseContext = mp_context

        self._result_class = result_class

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

    def run(self, test):
        raise NotImplementedError

    def discover_and_run(self,
                         start_dir,
                         pattern='test*.py',
                         top_level_dir=None,
                         result_path: pathlib.Path = None):

        if result_path is None:
            result_path = pathlib.Path(f'testruns') / pathlib.Path(f'testrun{uuid.uuid4().hex}')
        result_path.mkdir(parents=True, exist_ok=True)

        child_processes = list()
        process_conn_tuples: typing.List[typing.Tuple[
            multiprocessing.connection.Connection,
            multiprocessing.connection.Connection]
        ] = list()

        for _ in range(self._process_count):
            respective_parent_conn, child_conn = self._mp_context.Pipe(duplex=True)

            child_process = self._mp_context.Process(daemon=True,
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

        read_selector = selectors.DefaultSelector()
        for respective_parent_conn, child_conn in process_conn_tuples:
            read_selector.register(respective_parent_conn, selectors.EVENT_READ)

        for conn, _ in process_conn_tuples:
            try:
                conn.send(test_ids.pop())  # TODO: consider doing this in while True loop below
            except IndexError:
                break

        next_to_send = list()
        write_selectors = dict()

        for respective_parent_conn, child_conn in process_conn_tuples:
            assert respective_parent_conn.fileno() not in write_selectors
            specific_write_selector = selectors.DefaultSelector()
            specific_write_selector.register(respective_parent_conn, selectors.EVENT_WRITE)
            write_selectors[respective_parent_conn.fileno()] = specific_write_selector

        test_results = list()

        while True:
            read_events = read_selector.select(timeout=0.001)
            for key, mask in read_events:
                respective_parent_conn = key.fileobj
                test_results.append(respective_parent_conn.recv())

                next_to_send.append(respective_parent_conn)  # TODO: only do this when recv was successful

            if not test_ids:
                if len(next_to_send) == len(process_conn_tuples):  # TODO: compare sets of fileno instead
                    break

                continue

            for conn in copy.copy(next_to_send):
                if not test_ids:
                    break

                write_events = write_selectors[conn.fileno()].select(timeout=0.0001)
                for key, mask in write_events:
                    respective_parent_conn = key.fileobj

                    try:
                        respective_parent_conn.send(test_ids.pop())
                    except IndexError:
                        break
                    next_to_send.remove(conn)

        # cleanup starts here
        for respective_parent_conn, child_conn in process_conn_tuples:
            respective_parent_conn.send(-1)
            answer_for_shutdown_request = respective_parent_conn.recv()  # TODO: implement values other than -2
            assert answer_for_shutdown_request == -2
            respective_parent_conn.close()

        for child_process in child_processes:
            child_process.join(timeout=1)
            if child_process.is_alive():
                raise RuntimeError  # TODO: add message

        self._generate_html(test_results=test_results, result_path=result_path)

        return test_results

    @staticmethod
    def _generate_html(test_results: typing.List[mpunittest.result.MergeableResult],
                       result_path: pathlib.Path):
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
                    duration='no measurement',
                    log_file=test_result.log_file.name,
                    extra_class=extra_class,
                    text=extra_class.upper()
                )

        with open(result_path.joinpath('test_result.html'), 'w') as final_html_file:
            final_html_file.write(template_data.replace('{table_rows}', table_data))

    @staticmethod
    def process_target(child_conn, start_dir, pattern, top_level_dir, result_class, result_path):

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

            log_file_name = result_path.joinpath('test' + uuid.uuid4().hex + '.log')
            result = result_class(log_file_name=log_file_name)

            with tempfile.TemporaryDirectory() as temp_dir:
                temp_path = pathlib.Path(temp_dir).resolve()
                final_stderr_filename = temp_path.joinpath(stderr_filename)
                final_stdout_filename = temp_path.joinpath(stdout_filename)

                with \
                        open(final_stderr_filename, 'w') as stderr_file, \
                        open(final_stdout_filename, 'w') as stdout_file, \
                        contextlib.redirect_stderr(new_target=stderr_file), \
                        contextlib.redirect_stdout(new_target=stdout_file):
                    # execute test here
                    id_to_test_mapping[test_id](result)

                    stderr_file.flush()
                    stdout_file.flush()

                with \
                        open(log_file_name, 'w') as merged, \
                        open(final_stderr_filename, 'r') as stderr_file, \
                        open(final_stdout_filename, 'r') as stdout_file:
                    if len(result.test_id_to_result_mapping) == 1:
                        id_to_write = list(result.test_id_to_result_mapping.keys())[0]
                        merged.write(f'Log for test with id {id_to_write} run at {time_str}: \n\n')
                    else:
                        ids_to_write = set(result.test_id_to_result_mapping.keys())
                        merged.write(f'Log for tests with ids {ids_to_write} run at {time_str}: \n\n')
                    merged.write('#' * 10 + ' ' * 2 + 'stderr' + ' ' * 2 + '#' * 10 + '\n\n')
                    MergingRunner.copy_content_with_limited_buffer(stderr_file, merged)
                    merged.write('\n' + '#' * 30 + '\n\n')
                    merged.write('#' * 10 + ' ' * 2 + 'stdout' + ' ' * 2 + '#' * 10 + '\n\n')
                    MergingRunner.copy_content_with_limited_buffer(stdout_file, merged)
                    merged.write('\n' + '#' * 30 + '\n\n')
                    merged.write(f'Overall result: {result.overall_result()}\n')

            child_conn.send(result)

        child_conn.send(-2)

    @staticmethod
    def copy_content_with_limited_buffer(src, dst, buffer_size=1000):

        while True:
            data = src.read(buffer_size)
            if not data:
                break
            dst.write(data)
