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

import pathlib

import mpunittest.runner

if __name__ == '__main__':
    import mpunittest.tests.dummy.dirs

    r = mpunittest.runner.MergingRunner(process_count=7).discover_and_run(
        start_dir=pathlib.Path(mpunittest.tests.dummy.dirs.__file__).parent.resolve(),
        pattern="*test.py",
        html_result_assets=mpunittest.runner.HtmlResultAssets(
            document_title='test1',
            document_file_name='test2',
            result_path=None  # this will cause usage of the default dir
        )
    )

    print(r)
