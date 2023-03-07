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

import itertools
import multiprocessing
import typing
import unittest
import unittest.mock

import mpunittest.comm


class TestComm(unittest.TestCase):

    def test_shared_mem_buf(self):

        for i, j in itertools.product(range(1, 10), range(3, 12)):
            with unittest.mock.patch(target='mpunittest.comm._BYTES_PER_TRANSMISSION_CODE',
                                     new=i), \
                    unittest.mock.patch(target='mpunittest.comm._DURATION_SIZE',
                                        new=i * 7):

                sh_mem_comm = mpunittest.comm.SharedMemoryCommunicator(code_count=j,
                                                                       mp_context=multiprocessing.get_context('spawn'))
                try:
                    self.assertEqual(bytearray([0] * i * j),
                                     bytearray(sh_mem_comm._shmm_for_results.buf[0:i * j]))

                    self.assertEqual(bytearray([0] * i * 7 * j),
                                     bytearray(sh_mem_comm._shmm_for_durations.buf[0:i * 7 * j]))

                    sh_mem_comm.get()

                    self.assertEqual(bytearray([0] * (i - 1) + [7] + [0] * i * (j - 1)),
                                     bytearray(sh_mem_comm._shmm_for_results.buf[0:i * j]))

                    sh_mem_comm.get()

                    self.assertEqual(bytearray(([0] * (i - 1) + [7]) * 2 + [0] * i * (j - 2)),
                                     bytearray(sh_mem_comm._shmm_for_results.buf[0:i * j]))

                    sh_mem_comm.get()

                    self.assertEqual(bytearray(([0] * (i - 1) + [7]) * 3 + [0] * i * (j - 3)),
                                     bytearray(sh_mem_comm._shmm_for_results.buf[0:i * j]))

                    self.assertEqual(bytearray([0] * i * 7 * j),
                                     bytearray(sh_mem_comm._shmm_for_durations.buf[0:i * 7 * j]))

                    sh_mem_comm.put(1, mpunittest.comm.TransmissionCode.FAIL, 50)

                    self.assertEqual(bytearray([0] * i * 7 + [0] * (i * 7 - 1) + [50] + [0] * i * 7 * (j - 2)),
                                     bytearray(sh_mem_comm._shmm_for_durations.buf[0:i * 7 * j]))

                    self.assertEqual(
                        bytearray([0] * (i - 1) + [7] + [0] * (i - 1) + [30] + [0] * (i - 1) + [7] + [0] * i * (j - 3)),
                        bytearray(sh_mem_comm._shmm_for_results.buf[0:i * j]))

                    results: typing.List[
                        typing.Tuple[int, mpunittest.comm.TransmissionCode, int]] = sh_mem_comm.get_results()

                    self.assertEqual(
                        [(0, mpunittest.comm.TransmissionCode.STARTED, 0),
                         (1, mpunittest.comm.TransmissionCode.FAIL, 50),
                         (2, mpunittest.comm.TransmissionCode.STARTED, 0),
                         *((k + 3, mpunittest.comm.TransmissionCode.INITIAL, 0) for k in range(j - 3))
                         ],
                        results)

                    self.assertFalse(sh_mem_comm.is_finished())

                    for m in range(j - 3):
                        sh_mem_comm.get()
                        sh_mem_comm.put(index=m + 3,
                                        transmission_code=mpunittest.comm.TransmissionCode.PASS,
                                        duration=m)

                    results: typing.List[
                        typing.Tuple[int, mpunittest.comm.TransmissionCode, int]] = sh_mem_comm.get_results()

                    self.assertEqual(
                        [(0, mpunittest.comm.TransmissionCode.STARTED, 0),
                         (1, mpunittest.comm.TransmissionCode.FAIL, 50),
                         (2, mpunittest.comm.TransmissionCode.STARTED, 0),
                         *((k + 3, mpunittest.comm.TransmissionCode.PASS, k) for k in range(j - 3))
                         ],
                        results)

                    sh_mem_comm.put(index=0,
                                    transmission_code=mpunittest.comm.TransmissionCode.FAIL,
                                    duration=13)
                    sh_mem_comm.put(index=2,
                                    transmission_code=mpunittest.comm.TransmissionCode.FAIL,
                                    duration=17)

                    self.assertTrue(sh_mem_comm.is_finished())

                finally:
                    try:
                        sh_mem_comm.close()
                    finally:
                        sh_mem_comm.unlink()


if __name__ == '__main__':
    unittest.main()
