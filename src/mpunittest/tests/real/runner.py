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

import os
import pathlib
import tempfile
import unittest
import unittest.mock

import mpunittest.runner
import mpunittest.result
import mpunittest.tests.dummy.dirs


class TestRunner(unittest.TestCase):

    def test_flatten(self):

        test_case_1 = unittest.mock.Mock()
        test_case_2 = unittest.mock.Mock()
        test_case_3 = unittest.mock.Mock()

        test_suite = unittest.TestSuite()
        test_suite.addTest(test_case_1)
        test_suite.addTest(test_case_2)

        self.assertEqual(
            (test_case_1, test_case_2),
            tuple(mpunittest.runner.MergingRunner.flatten(test_suite))
        )

        self.assertEqual(
            (test_case_3,),
            tuple(mpunittest.runner.MergingRunner.flatten(test_case_3))
        )

    def test_discover_and_run(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)

            results = mpunittest.runner.MergingRunner().discover_and_run(
                start_dir=pathlib.Path(mpunittest.tests.dummy.dirs.__file__).parent.resolve(),
                pattern="*test.py",
                result_path=temp_path
            )

            result_mapping = dict()

            for result in results:
                for test_id, individual_result in result.test_id_to_result_mapping.items():
                    self.assertNotIn(test_id, result_mapping)
                    result_mapping[test_id] = individual_result

            self.assertEqual(5, len(set(result_mapping.keys())))
            self.assertCountEqual([mpunittest.result.MergeableResult.Result.FAIL,
                                   mpunittest.result.MergeableResult.Result.FAIL,
                                   mpunittest.result.MergeableResult.Result.PASS,
                                   mpunittest.result.MergeableResult.Result.PASS,
                                   mpunittest.result.MergeableResult.Result.SKIPPED],
                                  result_mapping.values())

            self.assertNotIn('<!--', temp_path.joinpath('test_result.html').read_text())


if __name__ == '__main__':
    import mpunittest.tests.dummy.dirs

    r = mpunittest.runner.MergingRunner().discover_and_run(
        start_dir=pathlib.Path(mpunittest.tests.dummy.dirs.__file__).parent.resolve(),
        pattern="*test.py"
    )

    print(r)
