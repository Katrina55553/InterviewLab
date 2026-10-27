"""Operator recovery preserves account ownership and revokes credentials atomically."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.platform import auth, cli
from app.platform.models import EmailChallenge, LoginSession, User, new_id, utcnow
from tests.platform_db import isolated_database, postgres_test_url


@pytest.mark.parametrize("command", ["reset-password", "create-admin"])
def test_cli_validation_never_prints_hidden_password(monkeypatch, capsys, command):
    monkeypatch.setattr("sys.argv", ["cli", command, "--username", "learner"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: "tiny")
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "tiny" not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize("dialect", ["sqlite", "postgresql"])
async def test_operator_password_reset_revokes_sessions_and_codes(tmp_path, monkeypatch, dialect, capsys):
    if dialect == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for PostgreSQL maintenance tests")
    async with isolated_database(dialect, tmp_path) as database:
        monkeypatch.setattr(cli, "async_session_maker", database.sessions)
        async with database.sessions() as db, db.begin():
            owner = User(username="legacy@example.test", email="verified@example.test",
                         password_hash=auth.hash_password("old-password-123"), role="admin")
            other = User(username="other", password_hash=auth.hash_password("other-password-123"), role="user")
            db.add_all([owner, other])
            await db.flush()
            owner_id, other_id = owner.id, other.id
            db.add_all([
                LoginSession(id="a" * 64, user_id=owner.id, csrf_token="owner", expires_at=utcnow() + timedelta(hours=1)),
                LoginSession(id="b" * 64, user_id=other.id, csrf_token="other", expires_at=utcnow() + timedelta(hours=1)),
                EmailChallenge(id="c" * 64, email=owner.email, purpose="reset", user_id=owner.id,
                               generation=new_id(), code_hash="d" * 64, status="sent", attempts=0,
                               expires_at=utcnow() + timedelta(minutes=10), resend_at=utcnow()),
            ])
        await cli.reset_password("LEGACY@example.test", "new-password-456")
        async with database.sessions() as db:
            changed = await db.get(User, owner_id)
            assert auth.verify_password("new-password-456", changed.password_hash)
            assert not auth.verify_password("old-password-123", changed.password_hash)
            assert changed.role == "admin" and changed.email == "verified@example.test"
            assert await db.get(LoginSession, "a" * 64) is None
            assert (await db.get(LoginSession, "b" * 64)).user_id == other_id
            assert not list(await db.scalars(select(EmailChallenge)))
        assert "new-password-456" not in capsys.readouterr().out
        with pytest.raises(ValueError, match="用户不存在"):
            await cli.reset_password("missing-user", "valid-password-123")
        async with database.sessions() as db:
            assert (await db.get(LoginSession, "b" * 64)).user_id == other_id
