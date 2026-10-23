"""Regenerate Python 3.12 dependency locks; use --upgrade to refresh versions."""

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upgrade", action="store_true", help="Refresh all allowed dependency versions")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for name in ("requirements", "requirements-dev"):
        command = [
            sys.executable, "-m", "uv", "pip", "compile",
            "--universal", "--python-version", "3.12", "--generate-hashes",
            "--custom-compile-command", "python scripts/lock_dependencies.py",
            "--output-file", f"{name}.txt", f"{name}.in", "--quiet",
        ]
        if args.upgrade:
            command.append("--upgrade")
        print(f"Locking {name}.in", flush=True)
        subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    main()
