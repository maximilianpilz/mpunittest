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

import mpunittest.result
import mpunittest.runner
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

    def test_time_conversion(self):

        self.assertEqual('0.000000001 ' + mpunittest.runner.TimeUnit.SECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             1,
                             mpunittest.runner.TimeUnit.SECONDS)
                         )

        self.assertEqual('0.1 ' + mpunittest.runner.TimeUnit.SECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             int(10 ** 8),
                             mpunittest.runner.TimeUnit.SECONDS)
                         )

        self.assertEqual('1 ' + mpunittest.runner.TimeUnit.SECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             int(10 ** 9),
                             mpunittest.runner.TimeUnit.SECONDS)
                         )

        self.assertEqual('11.67 ' + mpunittest.runner.TimeUnit.SECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             1167 * int(10 ** 7),
                             mpunittest.runner.TimeUnit.SECONDS)
                         )

        self.assertEqual('1.55 ' + mpunittest.runner.TimeUnit.MILLISECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             1550000,
                             mpunittest.runner.TimeUnit.MILLISECONDS)
                         )

        self.assertEqual('1 ' + mpunittest.runner.TimeUnit.MILLISECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             1000000,
                             mpunittest.runner.TimeUnit.MILLISECONDS)
                         )

        self.assertEqual('0.5 ' + mpunittest.runner.TimeUnit.MILLISECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             500000,
                             mpunittest.runner.TimeUnit.MILLISECONDS)
                         )

        self.assertEqual('0.5 ' + mpunittest.runner.TimeUnit.MICROSECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             500,
                             mpunittest.runner.TimeUnit.MICROSECONDS)
                         )

        self.assertEqual('5 ' + mpunittest.runner.TimeUnit.MICROSECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             5000,
                             mpunittest.runner.TimeUnit.MICROSECONDS)
                         )

        self.assertEqual('50 ' + mpunittest.runner.TimeUnit.MICROSECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             50000,
                             mpunittest.runner.TimeUnit.MICROSECONDS)
                         )

        self.assertEqual('500 ' + mpunittest.runner.TimeUnit.NANOSECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             500,
                             mpunittest.runner.TimeUnit.NANOSECONDS)
                         )

        self.assertEqual('5000000000 ' + mpunittest.runner.TimeUnit.NANOSECONDS.name.lower(),
                         mpunittest.runner.ns_to_time_unit(
                             5000000000,
                             mpunittest.runner.TimeUnit.NANOSECONDS)
                         )

    def test_discover_and_run(self):

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)

            results = mpunittest.runner.MergingRunner().discover_and_run(
                start_dir=pathlib.Path(mpunittest.tests.dummy.dirs.__file__).parent.resolve(),
                pattern="*test.py",
                html_result_assets=mpunittest.runner.HtmlResultAssets(
                    document_title=None,  # this will cause usage of the default title
                    document_file_name=None,  # this will cause usage of the default file name
                    result_path=temp_path
                )
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
    unittest.main()
