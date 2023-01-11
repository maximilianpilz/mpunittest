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
import contextlib
import os
import pathlib
import sys


@contextlib.contextmanager
def duplicate_stderr_to_file(file: pathlib.Path):
    with open(file, 'w') as f:
        with redirect_fd(f.fileno(), sys.stderr.fileno()):
            try:
                yield
            finally:
                sys.stderr.flush()


@contextlib.contextmanager
def duplicate_stdout_to_file(file: pathlib.Path):
    with open(file, 'w') as f:
        with redirect_fd(f.fileno(), sys.stdout.fileno()):
            try:
                yield
            finally:
                sys.stdout.flush()


@contextlib.contextmanager
def redirect_fd(dst_fd: int, src_fd: int):
    old_src_fd = os.dup(src_fd)

    try:
        os.dup2(dst_fd, src_fd)
        try:
            yield
        finally:
            os.dup2(old_src_fd, src_fd)  # reset
    finally:
        os.close(old_src_fd)
