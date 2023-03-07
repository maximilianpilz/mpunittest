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

import abc
import enum
import multiprocessing
import multiprocessing.shared_memory
import typing

_DURATION_SIZE = 64


class TransmissionCode(int, enum.Enum):
    INITIAL = 0b000000
    STARTED = 0b000111
    PASS = 0b011001
    FAIL = 0b011110
    SKIPPED = 0b101010
    ERROR = 0b101101


_BITS_OF_MAX_VALUE = 0xFF.bit_length()
assert _BITS_OF_MAX_VALUE >= 8


def _length_in_bytes(i: int) -> int:
    assert i >= 0

    if i == 0:
        return 1

    return (i.bit_length() + _BITS_OF_MAX_VALUE - 1) // _BITS_OF_MAX_VALUE


assert len(TransmissionCode) > 1
_BYTES_PER_TRANSMISSION_CODE = max(_length_in_bytes(e) for e in TransmissionCode)


class BaseCommunicator(metaclass=abc.ABCMeta):

    @abc.abstractmethod
    def __init__(self, code_count: int, mp_context: multiprocessing.context.BaseContext):
        assert code_count > 0

        self._code_count: int = code_count
        self._mp_context: multiprocessing.context.BaseContext = mp_context

    @abc.abstractmethod
    def get(self):
        raise NotImplementedError

    @abc.abstractmethod
    def put(self, index, transmission_code, duration):
        raise NotImplementedError

    @abc.abstractmethod
    def get_results(self) -> typing.List[typing.Tuple[int, TransmissionCode, int]]:
        raise NotImplementedError

    @abc.abstractmethod
    def is_finished(self):
        raise NotImplementedError

    @abc.abstractmethod
    def close(self):
        raise NotImplementedError

    @abc.abstractmethod
    def unlink(self):
        raise NotImplementedError


class SharedMemoryCommunicator(BaseCommunicator):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._from_bytes_kwargs = {'byteorder': 'big',
                                   'signed': False}
        self._to_bytes_kwargs_without_length = self._from_bytes_kwargs
        self._to_bytes_kwargs = {'length': _BYTES_PER_TRANSMISSION_CODE}
        self._to_bytes_kwargs.update(self._to_bytes_kwargs_without_length)

        self._shmm_for_results = multiprocessing.shared_memory.SharedMemory(
            create=True,
            size=self._code_count * _BYTES_PER_TRANSMISSION_CODE)

        self._shmm_for_durations = multiprocessing.shared_memory.SharedMemory(
            create=True,
            size=self._code_count * _DURATION_SIZE
        )

        self._lock = self._mp_context.Lock()

        with self._lock:
            self._shmm_for_results.buf[:self._code_count * _BYTES_PER_TRANSMISSION_CODE] = \
                b''.join([TransmissionCode.INITIAL.to_bytes(**self._to_bytes_kwargs)] * self._code_count)

            self._shmm_for_durations.buf[:self._code_count * _DURATION_SIZE] = bytearray(
                [0] * _DURATION_SIZE * self._code_count)

    def get(self) -> typing.Optional[int]:
        with self._lock:
            for i in range(self._code_count):
                start_index = i * _BYTES_PER_TRANSMISSION_CODE
                code = int.from_bytes(
                    self._shmm_for_results.buf[start_index:start_index + _BYTES_PER_TRANSMISSION_CODE],
                    **self._from_bytes_kwargs)
                if code == TransmissionCode.INITIAL:
                    self._shmm_for_results.buf[start_index:start_index + _BYTES_PER_TRANSMISSION_CODE] = \
                        TransmissionCode.STARTED.to_bytes(**self._to_bytes_kwargs)
                    return i

    def put(self, index, transmission_code: TransmissionCode, duration: int):
        assert 0 <= index < self._code_count

        result_index = index * _BYTES_PER_TRANSMISSION_CODE
        duration_index = index * _DURATION_SIZE

        with self._lock:
            assert int.from_bytes(
                self._shmm_for_results.buf[result_index:result_index + _BYTES_PER_TRANSMISSION_CODE],
                **self._from_bytes_kwargs) == TransmissionCode.STARTED, \
                'result input is only allowed after a start code has been written'

            self._shmm_for_results.buf[result_index:result_index + _BYTES_PER_TRANSMISSION_CODE] = \
                transmission_code.to_bytes(**self._to_bytes_kwargs)

            self._shmm_for_durations.buf[duration_index:duration_index + _DURATION_SIZE] = \
                duration.to_bytes(length=_DURATION_SIZE, **self._to_bytes_kwargs_without_length)

    def get_results(self) -> typing.List[typing.Tuple[int, TransmissionCode, int]]:
        results = list()

        with self._lock:
            for i in range(self._code_count):
                result_index = i * _BYTES_PER_TRANSMISSION_CODE
                code = TransmissionCode.from_bytes(
                    self._shmm_for_results.buf[result_index:result_index + _BYTES_PER_TRANSMISSION_CODE],
                    **self._from_bytes_kwargs)

                duration_index = i * _DURATION_SIZE
                duration = int.from_bytes(
                    self._shmm_for_durations.buf[duration_index:duration_index + _DURATION_SIZE],
                    **self._from_bytes_kwargs
                )
                results.append((
                    i,
                    code,
                    duration))

        return results

    def is_finished(self) -> bool:
        results = list()

        with self._lock:
            for i in range(self._code_count):
                result_index = i * _BYTES_PER_TRANSMISSION_CODE
                code = TransmissionCode.from_bytes(
                    self._shmm_for_results.buf[result_index:result_index + _BYTES_PER_TRANSMISSION_CODE],
                    **self._from_bytes_kwargs)
                results.append(
                    code in (TransmissionCode.PASS,
                             TransmissionCode.FAIL,
                             TransmissionCode.SKIPPED,
                             TransmissionCode.ERROR))

        return all(results) and len(results) == self._code_count

    def close(self):
        try:
            self._shmm_for_results.close()
        finally:
            self._shmm_for_durations.close()

    def unlink(self):
        try:
            self._shmm_for_results.unlink()
        finally:
            self._shmm_for_durations.unlink()


class PipeCommunicator(BaseCommunicator):
    _BLOCKING = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._initial_queue = self._mp_context.Queue(maxsize=self._code_count)
        self._result_queue = self._mp_context.Queue(maxsize=self._code_count)

        self._initial_lock = self._mp_context.Lock()
        self._result_lock = self._mp_context.Lock()

        # Acquiring the initial lock might not be necessary here,
        # when the initialization is finished before starting other processes,
        # but since we can not guarantee that behaviour and to be compatible with
        # future changes of process supervision, we still use the lock here
        with self._initial_lock:
            for i in range(self._code_count):
                self._initial_queue.put((i, TransmissionCode.INITIAL), block=self._BLOCKING)

    def get(self) -> typing.Optional[int]:
        with self._initial_lock:
            index, transmission_code = self._initial_queue.get(block=self._BLOCKING)

            if transmission_code == TransmissionCode.STARTED:
                self._initial_queue.put((index, transmission_code), block=self._BLOCKING)
                return None
            elif transmission_code == TransmissionCode.INITIAL:
                self._initial_queue.put((index, TransmissionCode.STARTED), block=self._BLOCKING)
                return index
            else:
                self._initial_queue.put((index, transmission_code), block=self._BLOCKING)
                raise ValueError('Got an unexpected value for transmission code from _initial_queue')

    def put(self, index: int, transmission_code: TransmissionCode, duration: int):
        assert 0 <= index < self._code_count

        with self._result_lock:
            self._result_queue.put((index, transmission_code, duration), block=self._BLOCKING)

    def get_results(self) -> typing.List[typing.Tuple[int, TransmissionCode, int]]:

        unordered_results = list()

        with self._result_lock:

            while len(unordered_results) < self._code_count:
                unordered_results.append(self._result_queue.get(block=self._BLOCKING))

            for result in unordered_results:
                self._result_queue.put(result, block=self._BLOCKING)

        assert len({i for i, _, __ in unordered_results}) == len(unordered_results) == self._code_count

        results = list()
        for index, result, duration in sorted(unordered_results, key=lambda e: e[0]):
            results.append((index, result, duration))

        return results

    def is_finished(self):
        with self._result_lock:
            # TODO: (consider) replacement, because it is unreliable according to its documentation
            return self._result_queue.full()

    def close(self):
        pass  # nothing to do here, because emptying and closing the queues once (in unlink) is enough

    def unlink(self):
        with self._initial_lock:
            for _ in range(self._code_count):
                self._initial_queue.get(block=self._BLOCKING)

            self._initial_queue.close()

        with self._result_lock:
            for _ in range(self._code_count):
                self._result_queue.get(block=self._BLOCKING)

            self._result_queue.close()
