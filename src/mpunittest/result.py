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
import time
import typing
import unittest

import mpunittest.comm


class SimpleResult(int, enum.Enum):  # TODO: change to enum.StrEnum in CPython 3.11
    PASS = 1
    FAIL = 2
    SKIPPED = 3
    ERROR = 4


TransmissionCodeToSimpleResult = {
    mpunittest.comm.TransmissionCode.PASS: SimpleResult.PASS,
    mpunittest.comm.TransmissionCode.FAIL: SimpleResult.FAIL,
    mpunittest.comm.TransmissionCode.SKIPPED: SimpleResult.SKIPPED,
    mpunittest.comm.TransmissionCode.ERROR: SimpleResult.ERROR
}


class SubProcessResult(unittest.TestResult):

    @property
    def time_spent(self):
        assert self._time_spent
        assert self._time_spent >= 0

        return self._time_spent

    @property
    def overall_result(self) -> typing.Tuple[
        typing.Literal[
            mpunittest.comm.TransmissionCode.PASS,
            mpunittest.comm.TransmissionCode.FAIL,
            mpunittest.comm.TransmissionCode.SKIPPED,
            mpunittest.comm.TransmissionCode.ERROR], str]:
        assert self.testsRun > 0
        assert self._time_spent >= 0
        assert len(self.skipped) <= self.testsRun

        if self.wasSuccessful() and (len(self.skipped) < self.testsRun):
            assert self.successes
            transmission_code = mpunittest.comm.TransmissionCode.PASS
        elif self.wasSuccessful() and (len(self.skipped) == self.testsRun):
            assert self.skipped
            transmission_code = mpunittest.comm.TransmissionCode.SKIPPED
        elif self.errors:
            assert self.errors
            transmission_code = mpunittest.comm.TransmissionCode.ERROR
        elif self.failures or self.unexpectedSuccesses:
            assert self.failures
            transmission_code = mpunittest.comm.TransmissionCode.FAIL
        else:
            raise NotImplementedError  # TODO: add parameter

        str_output = '\n'

        if self.errors:
            for test, err_str in self.errors:
                str_output += f'The test {test.id()} had an error.\n'
                if err_str:
                    str_output += f'Error:\n{err_str}\n'
                str_output += '\n'
        if self.failures:
            for test, err_str in self.failures:
                str_output += f'The test {test.id()} had a failure.\n'
                if err_str:
                    str_output += f'Failure:\n{err_str}\n'
                str_output += '\n'
        if self.unexpectedSuccesses:
            for test in self.unexpectedSuccesses:
                str_output += f'The test {test.id()} had a failure due to an unexpected success.\n\n'
        if self.skipped:
            for test, reason in self.skipped:
                str_output += f'The test {test.id()} was skipped.\n'
                str_output += f'Reason: {reason}\n\n'
        if self.successes:
            for test in self.successes:
                str_output += f'The test {test.id()} was successful.\n\n'

        return transmission_code, str_output

    def wasSuccessful(self) -> bool:
        return (super().wasSuccessful() and
                (self.successes or
                 self.expectedFailures or
                 self.skipped) and
                self.testsRun > 0)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        assert self.buffer is False

        self._time_spent: typing.Optional[int] = None
        assert not hasattr(self, 'successes')
        self.successes: typing.List[unittest.case.TestCase] = list()

    def startTestRun(self) -> None:
        assert self.buffer is False

        super().startTestRun()

        assert self._time_spent is None
        self._time_spent = -time.monotonic_ns()

    def stopTestRun(self) -> None:
        super().stopTestRun()

        self._time_spent += time.monotonic_ns()
        assert self._time_spent >= 0

    def startTest(self, test: unittest.case.TestCase) -> None:
        assert self.buffer is False

        super().startTest(test)

    @unittest.result.failfast
    def addError(self, *args, **kwargs) -> None:
        error_count = len(self.errors)

        super().addError(*args, **kwargs)

        assert len(self.errors) > error_count

        assert self.errors[-1]
        # print(self.errors[-1][1], file=sys.stderr, flush=True)

    @unittest.result.failfast
    def addFailure(self, *args, **kwargs) -> None:
        failure_count = len(self.failures)

        super().addFailure(*args, **kwargs)

        assert len(self.failures) > failure_count

        assert self.failures[-1]
        # print(self.failures[-1][1], file=sys.stderr, flush=True)

    def addSubTest(self, *args, **kwargs) -> None:
        error_count = len(self.errors)
        failure_count = len(self.failures)

        super().addSubTest(*args, **kwargs)

        assert (len(self.errors) > error_count) or (len(self.failures) > failure_count)

        # if len(self.errors) > error_count:
        #     print(self.errors[-1][1], file=sys.stderr, flush=True)
        # else:
        #     print(self.failures[-1][1], file=sys.stderr, flush=True)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test)

        self.successes.append(test)
