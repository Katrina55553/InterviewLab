"""Explicit platform maintenance: migrate, bootstrap admin, or reset development data."""

import argparse
import asyncio
import getpass
from pathlib import Path

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import Column, MetaData, String, Table, delete, select, update
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.database import Base, async_session_maker, engine
from app.platform.auth import Credentials, hash_password
from app.platform.models import EmailChallenge, LoginSession, Outbox, User, new_id


def migrate() -> None:
    command.upgrade(Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")), "head")


async def create_admin(username: str, password: str) -> None:
    credentials = Credentials(username=username, password=password)
    if "@" in credentials.username:
        raise ValueError("新用户名不能包含 @；邮箱留待登录后绑定")
    encoded = await asyncio.to_thread(hash_password, credentials.password)
    async with async_session_maker() as db:
        existing = (await db.execute(select(User).where(User.username == credentials.username))).scalar_one_or_none()
        if existing is not None:
            raise ValueError("用户名已存在；为避免意外修改账号，管理员创建已取消")
        db.add(User(username=credentials.username, password_hash=encoded, role="admin"))
        try:
            await db.commit()
        except IntegrityError as exc:
            raise ValueError("用户名已存在") from exc
    print(f"管理员 {credentials.username} 已创建")


async def reset_password(username: str, password: str) -> None:
    """Operator recovery for legacy users who have no verified email."""
    credentials = Credentials(username=username, password=password)
    encoded = await asyncio.to_thread(hash_password, credentials.password)
    async with async_session_maker() as db, db.begin():
        # Updating before revocation serializes with login/password commands.
        user_id = await db.scalar(
            update(User).where(User.username == credentials.username)
            .values(password_hash=encoded).returning(User.id)
        )
        if user_id is None:
            raise ValueError("用户不存在")
        await db.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
        await db.execute(delete(EmailChallenge).where(EmailChallenge.user_id == user_id))
    print(f"用户 {credentials.username} 的密码已重置，所有登录会话及待用邮箱验证码已失效")


async def probe_jobs():
    jobs = [new_id() for _ in range(3)]
    async with async_session_maker() as db, db.begin():
        for job_id, queue in zip(jobs, ("default", "prepare", "report"), strict=True):
            db.add(Outbox(id=job_id, kind="probe:" + queue, aggregate_id="operations", payload={},
                          status="pending", execution_status="queued"))
    async with asyncio.timeout(90):
        while True:
            async with async_session_maker() as db:
                statuses = list((await db.scalars(select(Outbox.execution_status).where(Outbox.id.in_(jobs)))).all())
                if len(statuses) == 3 and all(s == "completed" for s in statuses):
                    print("All three durable task queues completed their probes")
                    return
            await asyncio.sleep(.5)


async def reset_dev(confirmation: str | None) -> None:
    if settings.ENVIRONMENT != "development":
        raise ValueError("reset-dev 只允许在 ENVIRONMENT=development 时执行")
    if confirmation != "RESET-PLATFORM":
        raise ValueError("请显式指定 --confirm RESET-PLATFORM；此命令删除新平台数据，保留旧版表")
    # Import all platform mappings before sorting foreign-key dependencies.
    from app.platform import media_models, models  # noqa: F401

    platform_tables = [table for table in Base.metadata.sorted_tables if table.name.startswith("platform_")]
    version = Table("platform_alembic_version", MetaData(), Column("version_num", String(32), primary_key=True))
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=platform_tables))
        await connection.run_sync(lambda sync: version.drop(sync, checkfirst=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="InterviewLab 新平台维护工具")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="升级新平台数据库；不修改旧版表")
    commands.add_parser("check-config", help="验证生产密钥和运行配置")
    commands.add_parser("probe-jobs", help="验证 default、prepare、report 队列的持久任务完成")
    admin = commands.add_parser("create-admin", help="交互输入密码，创建管理员")
    admin.add_argument("--username", required=True)
    password_reset = commands.add_parser("reset-password", help="交互重置已有用户密码并撤销全部会话")
    password_reset.add_argument("--username", required=True)
    reset = commands.add_parser("reset-dev", help="删除并重建 platform_ 表；仅限开发环境")
    reset.add_argument("--confirm", choices=["RESET-PLATFORM"])
    args = parser.parse_args()
    try:
        if args.command == "migrate":
            migrate()
        elif args.command == "check-config":
            settings.validate_runtime()
            if not settings.is_production:
                raise ValueError("发布门禁要求 ENVIRONMENT=production")
        elif args.command == "probe-jobs":
            asyncio.run(probe_jobs())
        elif args.command == "create-admin":
            password = getpass.getpass("管理员密码（至少 8 位）: ")
            if password != getpass.getpass("再次输入密码: "):
                raise ValueError("两次密码不一致")
            asyncio.run(create_admin(args.username, password))
        elif args.command == "reset-password":
            password = getpass.getpass("新密码（至少 8 位）: ")
            if password != getpass.getpass("再次输入新密码: "):
                raise ValueError("两次密码不一致")
            asyncio.run(reset_password(args.username, password))
        else:
            asyncio.run(reset_dev(args.confirm))
            migrate()
            print("新平台开发数据已重置；旧版表未改动")
    except ValidationError as exc:
        # ValidationError's default text includes input_value, including passwords.
        parser.error("；".join(error["msg"] for error in exc.errors(include_input=False)))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
