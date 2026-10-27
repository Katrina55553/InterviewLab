"""Exercise fail-closed release/recovery commands without touching a Docker daemon."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SHA = "a" * 40
SECRET = "private-test-key-" * 4
BASH = shutil.which("bash") if os.name != "nt" else next(
    (str(path) for path in (Path("D:/Git/bin/bash.exe"), Path("C:/Program Files/Git/bin/bash.exe")) if path.exists()),
    None,
)


class RedactedEnvironment(dict):
    def __repr__(self):
        return "<redacted test environment>"


@pytest.fixture
def release(tmp_path):
    if not BASH:
        pytest.skip("Release command tests require Bash")
    root = tmp_path / "host"
    (root / "backend").mkdir(parents=True)
    (root / "backend/.env").write_text(f"SECRET_KEY={SECRET}\n", encoding="utf-8")
    (root / "current-release.env").write_text("RELEASE=previous\n", encoding="utf-8")
    target = root / "releases" / SHA
    shutil.copytree(SCRIPTS, target / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    (target / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    for name in ("backend.tar.gz", "frontend.tar.gz"):
        (target / name).write_bytes(b"fake tested image")
    import hashlib

    (target / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256((target / name).read_bytes()).hexdigest()}  {name}\n"
        for name in ("backend.tar.gz", "frontend.tar.gz")
    ), encoding="utf-8", newline="\n")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # The mock records actions and supplies only private fixture output.
    (binaries / "docker").write_text(
        '#!/usr/bin/env bash\nset -eu\n'
        'printf "%s\\n" "$*" >> "$COMMAND_LOG"\n'
        'if [[ -n "${DOCKER_FAIL:-}" && "$*" == *"$DOCKER_FAIL"* ]]; then exit 19; fi\n'
        'case "$*" in\n'
        '  *" pg_dump "*" -Fc "*) printf "database fixture";;\n'
        '  *" pg_dump "*"--schema-only"*) printf "%s\\n" "${DOCKER_SCHEMA:-CREATE TABLE fixture (id integer);}";;\n'
        '  *" psql "*) cat >/dev/null; printf "%s\\n" "${DOCKER_COUNTS:-public.fixture 1}";;\n'
        '  *" archive /app/uploads"*) cat >/dev/null; printf "archive fixture";;\n'
        '  *" manifest /app/uploads"*) cat >/dev/null; printf "{}\\n";;\n'
        '  *" restore /restore "*) cat >/dev/null; printf "Upload digests verified\\n";;\n'
        '  *" pg_restore "*) cat >/dev/null;;\n'
        'esac\n', encoding="utf-8", newline="\n",
    )
    (binaries / "docker").chmod(0o755)
    # flock is supplied by util-linux on deployment hosts, absent in Git Bash.
    if os.name == "nt":
        (binaries / "flock").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8", newline="\n")
    env = RedactedEnvironment(os.environ, PATH=str(binaries) + os.pathsep + os.environ["PATH"], COMMAND_LOG=str(tmp_path / "commands.log"))
    return root, target, env


def run_script(name, args, env):
    return subprocess.run([BASH, str(SCRIPTS / name), *map(str, args)], env=env, capture_output=True, text=True, timeout=30)


def commands(env):
    path = Path(env["COMMAND_LOG"])
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def deploy(release, **overrides):
    root, _, env = release
    result = run_script("deploy-release.sh", [SHA, root], dict(env, **overrides))
    return result


def test_deploy_backs_up_stopped_database_uploads_and_secret_before_migration(release):
    result = deploy(release)
    assert result.returncode == 0, result.stderr
    root, _, env = release
    log = commands(env)
    stop = next(i for i, line in enumerate(log) if " stop " in line)
    start_db = next(i for i, line in enumerate(log) if line.endswith("postgres redis"))
    dump = next(i for i, line in enumerate(log) if " -Fc " in line)
    migrate = next(i for i, line in enumerate(log) if line.endswith("run --rm migrate"))
    assert "celery-beat" in log[stop]
    assert stop < start_db < dump < migrate
    backups = list((root / "backups").iterdir())
    assert len(backups) == 1
    backup = backups[0]
    assert not (backup / ".incomplete").exists()
    assert (backup / "backend.env").read_text() == f"SECRET_KEY={SECRET}\n"
    assert (backup / "source-release.env").read_text() == "RELEASE=previous\n"
    assert "uploads.tar.gz" in (backup / "SHA256SUMS").read_text()
    assert "backend.env" in (backup / "SHA256SUMS").read_text()
    assert SECRET not in result.stdout + result.stderr
    if os.name != "nt":
        assert backup.stat().st_mode & 0o777 == 0o700
        assert (backup / "backend.env").stat().st_mode & 0o777 == 0o600


def test_backup_failure_blocks_migration_and_preserves_release_marker(release):
    result = deploy(release, DOCKER_FAIL=" -Fc ")
    assert result.returncode != 0
    root, _, env = release
    assert not any(line.endswith("run --rm migrate") for line in commands(env))
    assert (root / "current-release.env").read_text() == "RELEASE=previous\n"
    assert any((backup / ".incomplete").exists() for backup in (root / "backups").iterdir())


def test_rollback_incompatible_schema_keeps_running_services(release):
    root, _, env = release
    result = run_script("rollback-release.sh", [SHA, root], dict(env, DOCKER_FAIL="asyncio.run(init_db())"))
    assert result.returncode != 0
    assert not any(" stop " in line or " up " in line for line in commands(env))
    assert (root / "current-release.env").read_text() == "RELEASE=previous\n"


def test_rollback_replaces_scheduler_and_probes_queues(release):
    root, _, env = release
    result = run_script("rollback-release.sh", [SHA, root], env)
    assert result.returncode == 0, result.stderr
    log = commands(env)
    assert "celery-beat" in next(line for line in log if " stop " in line)
    assert "celery-beat" in next(line for line in log if " up " in line)
    assert any(line.endswith("probe-jobs") for line in log)
    assert not any(line.endswith("migrate") for line in log)


@pytest.mark.parametrize("damage", ["checksum", "missing_checksum", "incomplete"])
def test_restore_refuses_damaged_backup_before_creating_resources(release, damage):
    result = deploy(release)
    assert result.returncode == 0, result.stderr
    root, _, env = release
    backup = next((root / "backups").iterdir())
    if damage == "checksum":
        (backup / "uploads.tar.gz").write_bytes(b"tampered")
    elif damage == "missing_checksum":
        path = backup / "SHA256SUMS"
        path.write_text("".join(line for line in path.read_text().splitlines(keepends=True) if "uploads.tar.gz" not in line), newline="\n")
    else:
        (backup / ".incomplete").touch()
    Path(env["COMMAND_LOG"]).unlink()
    result = run_script("restore-drill.sh", [backup, "fixture-backend"], env)
    assert result.returncode != 0
    assert commands(env) == []


@pytest.mark.parametrize("failure", [None, "counts", "schema", "pg_restore"])
def test_restore_uses_fresh_disconnected_resources_and_always_cleans_up(release, failure):
    result = deploy(release)
    assert result.returncode == 0, result.stderr
    root, _, env = release
    backup = next((root / "backups").iterdir())
    overrides = {"counts": {"DOCKER_COUNTS": "different 2"}, "schema": {"DOCKER_SCHEMA": "different schema"},
                 "pg_restore": {"DOCKER_FAIL": " pg_restore "}}
    Path(env["COMMAND_LOG"]).unlink()
    result = run_script("restore-drill.sh", [backup, "fixture-backend"], dict(env, **overrides.get(failure, {})))
    assert (result.returncode == 0) == (failure is None), result.stdout + result.stderr
    log = commands(env)
    assert all("--network none" in line and "--publish" not in line for line in log if line.startswith("run "))
    assert len([line for line in log if line.startswith("volume create ")]) == 2
    assert len([line for line in log if line.startswith("volume rm ")]) == 2
    assert len([line for line in log if line.startswith("rm -f resumeagent-restore-")]) == 1


def test_upload_restore_detects_modified_content_and_rejects_archive_traversal(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backup_files", SCRIPTS / "backup-files.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = tmp_path / "uploads"
    source.mkdir()
    (source / "resume.txt").write_bytes(b"private fixture")
    expected = tmp_path / "files.json"
    expected.write_text(json.dumps(helper.manifest(source)), encoding="utf-8")
    archive = tmp_path / "uploads.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(source, arcname=".")
    config = tmp_path / "backend.env"
    config.write_text(f"SECRET_KEY={SECRET}\n", encoding="utf-8")
    restored = tmp_path / "restored"
    restored.mkdir()
    monkeypatch.setattr(sys, "argv", ["backup-files.py", "restore", str(restored), str(archive), str(expected), str(config)])
    helper.main()
    assert (restored / "resume.txt").read_bytes() == b"private fixture"
    shutil.rmtree(restored)
    restored.mkdir()
    expected.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="digests differ"):
        helper.main()
    shutil.rmtree(restored)
    restored.mkdir()
    with tarfile.open(archive, "w:gz") as stream:
        entry = tarfile.TarInfo("../escaped.txt")
        entry.size = 1
        stream.addfile(entry, io.BytesIO(b"x"))
    with pytest.raises(tarfile.OutsideDestinationError):
        helper.main()
    assert not (tmp_path / "escaped.txt").exists()


def test_credential_verifier_checks_nested_snapshots_and_rejects_wrong_key(capsys):
    spec = importlib.util.spec_from_file_location("backup_files", SCRIPTS / "backup-files.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    cipher = Fernet(Fernet.generate_key())
    plaintext = b"private-provider-fixture"
    encrypted = cipher.encrypt(plaintext).decode()
    snapshot = {"config_snapshot": {"nested": [{"encrypted_key": encrypted}]}, "encrypted_data": encrypted}
    assert helper.verify_credentials(snapshot, cipher) == 2
    with pytest.raises(InvalidToken):
        helper.verify_credentials(snapshot, Fernet(Fernet.generate_key()))
    captured = capsys.readouterr()
    assert plaintext.decode() not in captured.out + captured.err
