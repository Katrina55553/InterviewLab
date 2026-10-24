"""Explicit offline credential re-encryption; never changes configuration history versions."""

import asyncio
import base64
import copy
import getpass
import hashlib

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.platform.models import ConfigRevision, Interview, Provider, Resume, UserAISettings, UserModelCredential


def cipher(secret):
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def transform(value, old, new):
    if isinstance(value, list):
        return [transform(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: new.encrypt(old.decrypt(item.encode())).decode() if key == "encrypted_key" and item
                else transform(item, old, new) for key, item in value.items()}
    return value


async def reencrypt(old_secret, new_secret):
    if len(new_secret) < 32 or new_secret == old_secret:
        raise ValueError("新密钥必须为至少32字符的不同随机值")
    old, new = cipher(old_secret), cipher(new_secret)
    # Run with all application writers stopped and a verified backup.
    # One failure rolls back every encrypted field, including old frozen credentials.
    async with async_session_maker() as db, db.begin():
        for model in (Provider, UserAISettings, UserModelCredential):
            for row in (await db.scalars(select(model).with_for_update())).all():
                if row.encrypted_key:
                    row.encrypted_key = new.encrypt(old.decrypt(row.encrypted_key.encode())).decode()
        for model, field in ((ConfigRevision, "config"), (Interview, "config_snapshot"), (Resume, "config_snapshot")):
            for row in (await db.scalars(select(model).with_for_update())).all():
                setattr(row, field, transform(copy.deepcopy(getattr(row, field)), old, new))


if __name__ == "__main__":
    if input("停止所有写入并完成备份后，输入 REENCRYPT-PLATFORM: ") != "REENCRYPT-PLATFORM":
        raise SystemExit("已取消")
    value = getpass.getpass("新的稳定随机 SECRET_KEY: ")
    if value != getpass.getpass("再次输入: "):
        raise SystemExit("输入不一致")
    asyncio.run(reencrypt(settings.SECRET_KEY, value))
    print("凭据已在同一事务重加密；将运行配置 SECRET_KEY 更新为新值后重启。未输出密钥。")
