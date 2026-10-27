"""Provision disposable users through the test database, without sending real mail."""

import asyncio
import json
import sys

from app.platform import auth
from app.platform.models import User, UserAISettings, UserModelCredential


async def provision_user(username: str, password: str, *, fixture_model: bool = False) -> None:
    if fixture_model:
        from app.platform.admin import encrypt_key
        from app.platform.user_settings import ModelSelection
        from tests.engineering_runtime import (
            FIXTURE_API_KEY,
            FIXTURE_BASE_URL,
            assert_isolated_environment,
            fixture_selection,
        )

        assert_isolated_environment()
        selection = ModelSelection.model_validate(fixture_selection()).model_dump(exclude_none=True)
        encrypted_key = encrypt_key(FIXTURE_API_KEY)
    credentials = auth.Credentials(username=username, password=password)
    encoded = await asyncio.to_thread(auth.hash_password, credentials.password)
    async with auth.async_session_maker() as db, db.begin():
        user = User(username=credentials.username, password_hash=encoded, role="user")
        db.add(user)
        if fixture_model:
            await db.flush()
            db.add(UserModelCredential(user_id=user.id, provider_id=selection["providerId"], base_url=FIXTURE_BASE_URL,
                                       encrypted_key=encrypted_key, auth_method="api-key"))
            db.add(UserAISettings(user_id=user.id, base_url=FIXTURE_BASE_URL, model=selection["modelId"],
                                  encrypted_key=encrypted_key, selection=selection))


async def login_test_user(client, username: str, password: str) -> dict:
    await provision_user(username, password)
    response = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


if __name__ == "__main__":
    # CI/fault tests pass test-only credentials over stdin, never in process arguments.
    body = json.load(sys.stdin)
    asyncio.run(provision_user(body["username"], body["password"], fixture_model=body.get("fixture_model", False)))
