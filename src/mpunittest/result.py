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
import sys
import time
import traceback


class MergeableResult:  # TODO: check that instances of this are pickleable
    # TODO: consider adding failfast flag
    class Result(str, enum.Enum):  # TODO: change to enum.StrEnum in CPython 3.11
        UNKNOWN = 'unknown'
        PASS = 'pass'
        FAIL = 'fail'
        SKIPPED = 'skipped'

    def __init__(self, log_file_name):
        self.test_id_to_result_mapping = dict()  # TODO: sync with startTestRun
        self.last_error_mapping = dict()  # TODO: sync with startTestRun
        self.skip_reason_mapping = dict()  # TODO: sync with startTestRun
        self.time_spent_per_test_id = dict()  # TODO: sync with startTestRun

        self.log_file = log_file_name

    def printErrors(self):
        pass

    def startTest(self, test):
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.UNKNOWN
        self.time_spent_per_test_id[test.id()] = -time.monotonic_ns()

    def startTestRun(self):
        self.test_id_to_result_mapping = dict()
        self.last_error_mapping = dict()
        self.skip_reason_mapping = dict()
        self.time_spent_per_test_id = dict()

    def stopTest(self, test):
        self.time_spent_per_test_id[test.id()] += time.monotonic_ns()

    def stopTestRun(self):
        pass

    def addError(self, test, err):
        exc_info_string = ''.join(traceback.format_exception(*err))

        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
        self.last_error_mapping[test.id()] = exc_info_string
        print(exc_info_string, file=sys.stderr, flush=True)

    def addFailure(self, test, err):
        exc_info_string = ''.join(traceback.format_exception(*err))

        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
        self.last_error_mapping[test.id()] = exc_info_string
        print(exc_info_string, file=sys.stderr, flush=True)

    def addSubTest(self, test, subtest, err):
        # TODO: handle subtest parameter
        if err is not None:
            exc_info_string = ''.join(traceback.format_exception(*err))

            self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL
            self.last_error_mapping[test.id()] = exc_info_string
            print(exc_info_string, file=sys.stderr, flush=True)

    def addSuccess(self, test):
        if self.test_id_to_result_mapping[test.id()] == MergeableResult.Result.UNKNOWN:
            self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.PASS

    def addSkip(self, test, reason):
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.SKIPPED
        self.skip_reason_mapping[test.id()] = str(reason)  # ensure that it is pickleable

    def addExpectedFailure(self, test, err):
        pass  # TODO: implement, i.e. save what is relevant of the given information

    def addUnexpectedSuccess(self, test):
        self.test_id_to_result_mapping[test.id()] = MergeableResult.Result.FAIL

    def wasSuccessful(self):
        pass  # TODO: implement while considering expected failures

    def stop(self):
        pass

    def merge(self, other):
        raise NotImplementedError

    def overall_result(self):
        results = self.test_id_to_result_mapping.values()

        if self.Result.FAIL in results:
            return self.Result.FAIL
        elif self.Result.UNKNOWN in results:
            return self.Result.UNKNOWN
        elif all([result == self.Result.SKIPPED for result in results]):
            return self.Result.SKIPPED
        else:
            return self.Result.PASS
