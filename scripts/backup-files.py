"""Private upload backup/restore helper, streamed into the tested Python image."""

import base64
import hashlib
import json
import sys
import tarfile
from pathlib import Path


def manifest(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("Upload backups must not contain symlinks")
        if path.is_file():
            with path.open("rb") as stream:
                result[path.relative_to(root).as_posix()] = {
                    "bytes": path.stat().st_size,
                    "sha256": hashlib.file_digest(stream, "sha256").hexdigest(),
                }
        elif not path.is_dir():
            raise ValueError("Upload backups must contain only regular files/directories")
    return result


def backup_secret(path: Path) -> str:
    from dotenv import dotenv_values

    secret = dotenv_values(path).get("SECRET_KEY")
    if not secret or len(secret) < 32:
        raise ValueError("Backup lacks a production SECRET_KEY")
    return secret


def verify_credentials(value, cipher) -> int:
    checked = 0
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"encrypted_key", "encrypted_data"} and item:
                cipher.decrypt(item.encode())  # Discard plaintext immediately; never print it.
                checked += 1
            else:
                checked += verify_credentials(item, cipher)
    elif isinstance(value, list):
        checked += sum(verify_credentials(item, cipher) for item in value)
    return checked


def main() -> None:
    mode, root_text, *args = sys.argv[1:]
    root = Path(root_text)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("A real upload directory is required")
    if mode == "archive":
        manifest(root)  # Reject links and special files before writing any archive.
        with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
            archive.add(root, arcname=".")
    elif mode == "manifest":
        print(json.dumps(manifest(root), sort_keys=True))
    elif mode == "restore":
        archive_path, expected_path, env_path = map(Path, args)
        # A fresh Docker volume is the only supported extraction target.
        if any(root.iterdir()):
            raise ValueError("Restore target must be empty")
        with tarfile.open(archive_path, mode="r:gz") as archive:
            if any(member.issym() or member.islnk() or not (member.isfile() or member.isdir())
                   for member in archive.getmembers()):
                raise ValueError("Backup archive contains unsupported entries")
            archive.extractall(root, filter="data")
        if manifest(root) != json.loads(expected_path.read_text(encoding="utf-8")):
            raise ValueError("Restored upload file sizes or digests differ")
        backup_secret(env_path)
        print("Upload file digests and private configuration verified")
    elif mode == "credentials":
        from cryptography.fernet import Fernet

        secret = backup_secret(Path(args[0]))
        cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
        checked = sum(verify_credentials(json.loads(line), cipher) for line in sys.stdin if line.strip())
        print(f"Restored encrypted credential fields verified: {checked}")
    else:
        raise ValueError("Unknown backup operation")


if __name__ == "__main__":
    main()
