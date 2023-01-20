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
import argparse
import logging
import pathlib

import mpunittest.runner

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        prog='mpunittest.run',
        description='mpunittest.run executes unittests in the given '
                    'directory with the given amount of processes',
        epilog=f'mpunittest in version {mpunittest.VERSION}')

    parser.add_argument('directory')
    parser.add_argument('-p', '--pattern', required=False)
    parser.add_argument('-t', '--top_level_dir', required=False)
    parser.add_argument('-c', '--count', help='amount of processes to use', required=False)
    parser.add_argument('-d', '--daemon',
                        help='whether child processes shall be daemon processes',
                        action='store_true', required=False)
    parser.add_argument('-dt', '--document_title', required=False)
    parser.add_argument('-dfn', '--document_file_name', required=False)
    parser.add_argument('-o', '--output_dir', required=False)
    parser.add_argument('-l', '--log_level', required=False, default='INFO',
                        help='CRITICAL, FATAL, ERROR, WARNING, INFO, DEBUG')
    parser.add_argument('-da', '--default_asset', action='store_true', required=False)

    args = parser.parse_args()

    handler = logging.StreamHandler()

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    handler.setFormatter(formatter)
    handler.setLevel(getattr(logging, args.log_level))
    mpunittest.logging.logger.addHandler(handler)

    if args.count:
        merging_runner = mpunittest.runner.MergingRunner(process_count=int(args.count),
                                                         daemons=args.daemon)
    else:
        merging_runner = mpunittest.runner.MergingRunner(daemons=args.daemon)

    runner_args = {
        'start_dir': pathlib.Path(args.directory).resolve()
    }
    if args.pattern:
        runner_args['pattern'] = args.pattern
    if args.top_level_dir:
        runner_args['top_level_dir'] = args.top_level_dir

    asset_args = dict()

    if args.document_title:
        asset_args['document_title'] = args.document_title
    if args.document_file_name:
        asset_args['document_file_name'] = args.document_file_name
    if args.output_dir:
        asset_args['result_path'] = args.output_dir

    if args.default_asset and asset_args:
        raise RuntimeError('result asset parameters are inconsistent')

    if asset_args:
        runner_args['html_result_assets'] = mpunittest.runner.HtmlResultAssets(
            **asset_args
        )
    if args.default_asset:
        runner_args['html_result_assets'] = mpunittest.runner.HtmlResultAssets(None, None, None)

    result = merging_runner.discover_and_run(
        **runner_args
    )

    mpunittest.logging.logger.getChild('tests.real.manual').info(
        'run finished with return value %s', result)
