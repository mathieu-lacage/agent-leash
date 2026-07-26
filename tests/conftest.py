from __future__ import annotations

import os
import subprocess
import sys

import pytest


def pytest_configure(config: pytest.Config) -> None:
    os.environ["ALEASH_NO_BROWSER"] = "1"


def pytest_unconfigure(config: pytest.Config) -> None:
    os.environ.pop("ALEASH_NO_BROWSER", None)


# ---------------------------------------------------------------------------
# Cram test collector
# ---------------------------------------------------------------------------


class CramFailure(Exception):
    pass


class CramFile(pytest.File):
    def collect(self):
        yield CramItem.from_parent(self, name=self.path.name)


class CramItem(pytest.Item):
    def runtest(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "cram", "--preserve-env", str(self.parent.path)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CramFailure(result.stdout)

    def repr_failure(self, excinfo) -> str:
        if isinstance(excinfo.value, CramFailure):
            return str(excinfo.value)
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.parent.path, None, f"cram: {self.parent.path.name}"


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".t":
        return CramFile.from_parent(parent, path=file_path)
