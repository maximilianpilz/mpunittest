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
import enum
import pathlib
import sys
import time
import traceback
import types
import typing
import unittest


class MergeableResult:
    # TODO: consider adding failfast flag
    class Result(str, enum.Enum):  # TODO: change to enum.StrEnum in CPython 3.11
        UNKNOWN = 'unknown'
        PASS = 'pass'
        FAIL = 'fail'
        SKIPPED = 'skipped'

    def __init__(self, log_file_path: pathlib.Path):
        self.test_id_to_result_mapping: typing.Dict[str, MergeableResult.Result] = dict()  # TODO: sync with startTestRun
        self.last_error_mapping: typing.Dict[str, str] = dict()  # TODO: sync with startTestRun
        self.skip_reason_mapping: typing.Dict[str, str] = dict()  # TODO: sync with startTestRun
        self.time_spent_per_test_id: typing.Dict[str, int] = dict()  # TODO: sync with startTestRun

        self.log_file: pathlib.Path = log_file_path

    def __str__(self):
        return 'Overall result: ' + self.overall_result()

    def __repr__(self):
        return str(self.__class__.__name__) + ': ' + self.overall_result()

    def printErrors(self) -> None:
        pass

    def startTest(self, test: unittest.TestCase) -> None:
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.UNKNOWN
        self.time_spent_per_test_id[test.id()] = -time.monotonic_ns()

    def startTestRun(self) -> None:
        self.test_id_to_result_mapping = dict()
        self.last_error_mapping = dict()
        self.skip_reason_mapping = dict()
        self.time_spent_per_test_id = dict()

    def stopTest(self, test: unittest.TestCase) -> None:
        self.time_spent_per_test_id[test.id()] += time.monotonic_ns()

    def stopTestRun(self) -> None:
        pass

    def addError(
            self,
                 test: unittest.TestCase,
                 err: typing.Tuple[typing.Type[BaseException], BaseException, types.TracebackType]
    ) -> None:
        exc_info_string = ''.join(traceback.format_exception(*err))

        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
        self.last_error_mapping[test.id()] = exc_info_string
        print(exc_info_string, file=sys.stderr, flush=True)

    def addFailure(
            self,
                   test: unittest.TestCase,
                   err: typing.Tuple[typing.Type[BaseException], BaseException, types.TracebackType]
    ) -> None:
        exc_info_string = ''.join(traceback.format_exception(*err))

        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
        self.last_error_mapping[test.id()] = exc_info_string
        print(exc_info_string, file=sys.stderr, flush=True)

    def addSubTest(
            self,
                   test: unittest.TestCase,
                   subtest: unittest.TestCase,
                   err: typing.Tuple[typing.Type[BaseException], BaseException, types.TracebackType]
    ) -> None:
        # TODO: handle subtest parameter
        if err is not None:
            exc_info_string = ''.join(traceback.format_exception(*err))

            self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
            self.last_error_mapping[test.id()] = exc_info_string
            print(exc_info_string, file=sys.stderr, flush=True)

    def addSuccess(
            self,
                   test: unittest.TestCase
    ) -> None:
        if self.test_id_to_result_mapping[test.id()] == MergeableResult.Result.UNKNOWN:
            self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.PASS

    def addSkip(
            self,
                test: unittest.TestCase,
                reason: str
    ) -> None:
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.SKIPPED
        self.skip_reason_mapping[test.id()] = str(reason)  # ensure that it is pickleable

    def addExpectedFailure(
            self,
                           test: unittest.TestCase,
                           err: typing.Tuple[typing.Type[BaseException], BaseException, types.TracebackType]
    ) -> None:
        pass  # TODO: implement, i.e. save what is relevant of the given information

    def addUnexpectedSuccess(
            self,
                             test: unittest.TestCase
    ) -> None:
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL

    def wasSuccessful(self) -> bool:
        # TODO: consider expected failures
        return self.overall_result() == self.Result.PASS

    def stop(self) -> None:
        pass

    def merge(self, other):
        raise NotImplementedError

    def overall_result(self) -> Result:
        results = self.test_id_to_result_mapping.values()

        if self.Result.FAIL in results:
            return self.Result.FAIL
        elif self.Result.UNKNOWN in results:
            return self.Result.UNKNOWN
        elif all([result == self.Result.SKIPPED for result in results]):
            return self.Result.SKIPPED
        else:
            return self.Result.PASS
