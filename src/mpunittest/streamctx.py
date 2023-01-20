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
import typing


@contextlib.contextmanager
def redirect_stderr_to_file(file: pathlib.Path) -> typing.Generator:
    """
    Redirect sys.stderr to the given file.

    :param file: target of redirection
    :return: a generator, which can be converted to a respective contextmanager
    """
    assert file.is_file() or not file.exists()

    with open(file, 'w') as f:
        with redirect_fd(f.fileno(), sys.stderr.fileno()):
            try:
                yield
            finally:
                sys.stderr.flush()


@contextlib.contextmanager
def redirect_stdout_to_file(file: pathlib.Path) -> typing.Generator:
    """
    Redirect sys.stdout to the given file.

    :param file: target of redirection
    :return: a generator, which can be converted to a respective contextmanager
    """
    assert file.is_file() or not file.exists()

    with open(file, 'w') as f:
        with redirect_fd(f.fileno(), sys.stdout.fileno()):
            try:
                yield
            finally:
                sys.stdout.flush()


@contextlib.contextmanager
def redirect_fd(dst_fd: int, src_fd: int) -> typing.Generator:
    """
    Do the following:
        1. Allocate a new file descriptor and make it refer to the file description of src_fd
            i.e. create a backup of src_fd.
        2. Adjust the file descriptor src_fd so that it refers to the same open file description as dst_fd.
        3. Execute code of context via yield.
        4. Readjust the file descriptor src_fd so that it refers to the same open file description as it
            did at the beginning of the function i.e. reset it to its original state.
        5. Close the new file descriptor that was used as a backup.

    :param dst_fd: file descriptor that refers to the file description to be used for adjusting src_fd
    :param src_fd: file descriptor to be adjusted
    :return: a generator, which can be converted to a respective contextmanager
    """
    old_src_fd: int = os.dup(src_fd)  # backup

    try:
        os.dup2(dst_fd, src_fd)  # redirection
        try:
            yield  # code in the context of the context manager will be run here
        finally:
            os.dup2(old_src_fd, src_fd)  # reset
    finally:
        os.close(old_src_fd)  # since old_src_fd was only needed as a backup it can be closed now
