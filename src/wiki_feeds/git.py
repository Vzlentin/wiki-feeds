from __future__ import annotations

import subprocess
from pathlib import Path


def commit_and_push(vault_path: Path, message: str) -> None:
    """Stage new files in _raw/, commit, and push."""

    def run(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, cwd=vault_path, check=True, text=True)

    run(["git", "add", "_raw/"])
    # Check if there's anything to commit
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=vault_path,
    )
    if result.returncode == 0:
        return  # nothing staged

    run(["git", "commit", "-m", message])

    try:
        subprocess.run(["git", "push"], cwd=vault_path, check=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"  [git] Warning: push failed (exit {e.returncode}). Files are committed locally.")
