"""Advance the .centaur submodule pin to upstream HEAD and verify.

Run from the repo root:

    uv run scripts/sync_sdk.py

Pulls the latest ``.centaur`` (which is what the repo-root ``centaur_sdk``
symlink resolves through), runs the test suite against the new SDK, and
stages the pin bump for commit. Use when you want to upgrade the SDK to
the upstream HEAD; teammates will pick up the new pin via
``git submodule update --init --recursive`` once you commit + push.
"""

from __future__ import annotations

import os
import subprocess
import sys


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def capture(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> int:
    os.chdir(capture("git", "rev-parse", "--show-toplevel"))

    run("git", "submodule", "update", "--remote", ".centaur")
    sha = capture("git", "-C", ".centaur", "rev-parse", "--short", "HEAD")
    run("uv", "run", "pytest", "tests/")
    run("git", "add", ".centaur")

    print(f'\nPin advanced to {sha}. Commit with:\n  git commit -m "bump .centaur to {sha}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
