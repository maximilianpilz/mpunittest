**********
mpunittest
**********

|img1| |img2| |img3| |img4| |img5| |img6|

.. |img1| image:: https://img.shields.io/pypi/pyversions/mpunittest
   :alt: pypi python version(s)

.. |img2| image:: https://img.shields.io/pypi/implementation/mpunittest
   :alt: pypi implementation

.. |img3| image:: https://img.shields.io/pypi/status/mpunittest
   :alt: pypi development status

.. |img4| image:: https://img.shields.io/pypi/v/mpunittest
   :alt: pypi latest version

.. |img5| image:: https://img.shields.io/pypi/dm/mpunittest
   :alt: pypi downloads per month

.. |img6| image:: https://img.shields.io/badge/tested%20on-macos%20%7C%20ubuntu%20%7C%20windows-blue
   :alt: tested on which operating systems


| A Python library/application for running unittests in parallel and merging results.

Installation
============

To install the latest release from `PyPI <https://pypi.org/project/mpunittest/>`_,
simply run::

    pip install mpunittest

Or to install the latest development version, run::

     git clone https://github.com/maximilianpilz/mpunittest
     python -m pip install .

Quick Start
===========

An example for running with 10 workers and generating an html file containing the results::

    merging_runner = mpunittest.runner.MergingRunner(process_count=10)

    merging_runner.discover_and_run(
        start_dir=pathlib.Path('path_to_search_in').resolve(),
        pattern="*.py",
        html_result_assets=mpunittest.runner.HtmlResultAssets(
            document_title='title of the html',
            document_file_name='name_of_the_html_file',
            result_path='unittest_results'
        )
    )

An example for running with 10 workers and without any files being generated::

    merging_runner = mpunittest.runner.MergingRunner(process_count=10)

    result = merging_runner.discover_and_run(
        start_dir=pathlib.Path('path_to_search_in').resolve(),
        pattern="*.py"
    )

    print(result)

An example for turning on logging::

    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    mpunittest.logging.logger.addHandler(handler)

    # run tests here

    mpunittest.logging.logger.getChild('your.script').info('result: %s', result)

An example for running without writing additional python code::

    python -m mpunittest.run /Users/yourname/dev/python_projects/yourproject/src/tests -p "test*.py" -da -c 4

To see the help for running without writing additional python code::

    python -m mpunittest.run --help

Licensing
=========

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