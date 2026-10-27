"""Community reports retain evidence and moderation controls every public surface."""

import asyncio
import importlib.util
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, Table, create_engine, select

from app.platform import community
from app.platform.models import CommunityComment, CommunityPost, CommunityReport, User, utcnow
from tests.test_community import community_clients as community_clients
from tests.test_community import csrf, publish, register


@pytest_asyncio.fixture
async def moderation_clients(community_clients):
    owner, reader = community_clients
    await register(owner, "moderation_owner")
    await register(reader, "moderation_reader")
    async with httpx.AsyncClient(
        transport=owner._transport, base_url="http://test", headers={"Origin": "http://localhost:8000"}
    ) as admin:
        identity = await register(admin, "moderation_admin")
        async with community.async_session_maker() as db, db.begin():
            user = await db.get(User, identity["id"])
            user.role = "admin"
        yield owner, reader, admin


def report_body(target_id, target_type="post", **overrides):
    return {"id": str(uuid4()), "targetType": target_type, "targetId": target_id,
            "reason": "privacy", "details": "内容包含未获授权的个人信息", **overrides}


async def report(client, target_id, target_type="post", **overrides):
    body = report_body(target_id, target_type, **overrides)
    response = await client.post("/api/community/reports", json=body, headers=csrf(client))
    assert response.status_code in (200, 201), response.text
    return response.json(), body


async def review(client, report_id, action="hide", note="核实包含个人隐私，予以下架"):
    return await client.post(f"/api/community/admin/reports/{report_id}/review",
                             json={"action": action, "note": note}, headers=csrf(client))


@pytest.mark.asyncio
async def test_reports_require_identity_csrf_admin_and_validated_input(moderation_clients):
    owner, reader, admin = moderation_clients
    post, _ = await publish(owner)
    body = report_body(post["id"])
    async with httpx.AsyncClient(transport=owner._transport, base_url="http://test") as anonymous:
        assert (await anonymous.get("/api/community/reports/mine")).status_code == 401
        assert (await anonymous.get("/api/community/admin/reports")).status_code == 401
        assert (await anonymous.post("/api/community/reports", json=body)).status_code == 401
    assert (await reader.post("/api/community/reports", json=body)).status_code == 403
    assert (await reader.get("/api/community/admin/reports")).status_code == 403
    for invalid in ({"reason": "unknown"}, {"targetType": "user"}, {"targetId": "invalid"},
                    {"id": "invalid"}, {"details": "x" * 1001}, {"status": "resolved"},
                    {"reviewer": "forged"}):
        response = await reader.post("/api/community/reports", json={**body, **invalid}, headers=csrf(reader))
        assert response.status_code == 422, response.text
    for target_type in ("post", "comment"):
        response = await reader.post("/api/community/reports", json=report_body(str(uuid4()), target_type),
                                     headers=csrf(reader))
        assert response.status_code == 404, response.text
    assert (await admin.get("/api/community/admin/reports?status=unknown")).status_code == 422
    assert (await reader.get("/api/community/reports/mine?page=0")).status_code == 422
    created, _ = await report(reader, post["id"])
    path = f"/api/community/admin/reports/{created['id']}/review"
    assert (await reader.post(path, json={"action": "hide", "note": "无权限"}, headers=csrf(reader))).status_code == 403
    assert (await admin.post(path, json={"action": "hide", "note": "缺少令牌"})).status_code == 403
    for invalid in ({"action": "delete"}, {"note": "   "}, {"note": "x" * 1001}, {"status": "resolved"}):
        response = await admin.post(path, json={"action": "hide", "note": "审核理由", **invalid}, headers=csrf(admin))
        assert response.status_code == 422, response.text
    assert (await admin.get("/api/community/admin/reports")).json()["items"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_report_retries_are_unique_durable_and_account_scoped(moderation_clients):
    owner, reader, admin = moderation_clients
    post, _ = await publish(owner)
    other, _ = await publish(reader, title="另一位作者的面经")
    created, body = await report(reader, post["id"])
    assert created["targetType"] == "post" and created["targetId"] == post["id"]
    assert created["postId"] == post["id"] and created["status"] == "pending"
    assert created["targetTitle"] == post["title"] and created["targetContent"] == post["content"]
    assert created["reporter"] == "moderation_reader"
    assert created["reviewedAt"] is None and created["reviewer"] is None
    assert created["targetState"] == "visible"
    for replay_body in (body, {**body, "id": str(uuid4()), "details": "重复点击也只保留一条举报"}):
        replay = await reader.post("/api/community/reports", json=replay_body, headers=csrf(reader))
        assert replay.status_code == 200, replay.text
        assert replay.json()["id"] == created["id"] and replay.json()["details"] == body["details"]
    changed_request = await reader.post("/api/community/reports", json={**body, "details": "相同请求编号不能更换内容"},
                                        headers=csrf(reader))
    assert changed_request.status_code == 409, changed_request.text
    conflicting = await reader.post("/api/community/reports", json={**body, "targetId": other["id"]}, headers=csrf(reader))
    assert conflicting.status_code == 409, conflicting.text
    # Reusing another account's request identifier cannot reveal its report.
    conflicting = await owner.post("/api/community/reports", json=body, headers=csrf(owner))
    assert conflicting.status_code == 409, conflicting.text
    owned, _ = await report(owner, other["id"])
    assert [item["id"] for item in (await owner.get("/api/community/reports/mine")).json()["items"]] == [owned["id"]]
    assert [item["id"] for item in (await reader.get("/api/community/reports/mine")).json()["items"]] == [created["id"]]
    async with community.async_session_maker() as db:
        rows = list(await db.scalars(select(CommunityReport)))
        assert len(rows) == 2
    assert (await admin.get("/api/community/admin/reports?status=all")).json()["total"] == 2


@pytest.mark.asyncio
async def test_hiding_post_filters_every_public_surface_and_blocks_mutation(moderation_clients):
    owner, reader, admin = moderation_clients
    post, published_body = await publish(owner, company="待隐藏公司", tags=["待隐藏话题"])
    visible, _ = await publish(reader, company="保留公司", tags=["保留话题"])
    path = f"/api/community/posts/{post['id']}"
    await reader.put(path + "/like", headers=csrf(reader))
    comment_body = {"id": str(uuid4()), "content": "帖子下的原评论"}
    assert (await reader.post(path + "/comments", json=comment_body, headers=csrf(reader))).status_code == 201
    created, _ = await report(reader, post["id"])
    response = await review(admin, created["id"])
    assert response.status_code == 200, response.text
    reviewed = response.json()
    assert reviewed["status"] == "resolved" and reviewed["targetState"] == "hidden"
    assert reviewed["reviewer"] == "moderation_admin" and reviewed["reviewedAt"]
    assert reviewed["reviewNote"] == "核实包含个人隐私，予以下架"
    for client in (owner, reader, admin):
        assert (await client.get(path)).status_code == 404
        assert (await client.get(path + "/comments")).status_code == 404
        assert (await client.get("/api/community/posts", params={"q": "待隐藏", "company": "待隐藏公司"})).json()["total"] == 0
    assert (await reader.get("/api/community/posts")).json()["items"][0]["id"] == visible["id"]
    assert (await owner.get("/api/community/posts?tab=mine")).json()["total"] == 0
    assert (await reader.get("/api/community/posts?tab=liked")).json()["total"] == 0
    assert (await owner.get("/api/community/meta")).json() == {
        "companies": ["保留公司"], "topics": [{"tag": "保留话题", "count": 1}], "myPosts": 0, "myLikes": 0,
    }
    assert (await reader.get("/api/community/meta")).json()["myLikes"] == 0
    fields = {key: value for key, value in published_body.items() if key != "id"}
    assert (await owner.put(path, json={**fields, "title": "试图恢复"}, headers=csrf(owner))).status_code == 404
    assert (await owner.post("/api/community/posts", json=published_body, headers=csrf(owner))).status_code == 404
    assert (await reader.put(path + "/like", headers=csrf(reader))).status_code == 404
    assert (await reader.delete(path + "/like", headers=csrf(reader))).status_code == 404
    assert (await reader.post(path + "/comments", json=comment_body, headers=csrf(reader))).status_code == 404
    duplicate = await reader.post("/api/community/reports", json=report_body(post["id"]), headers=csrf(reader))
    assert duplicate.status_code == 200 and duplicate.json()["id"] == created["id"]
    assert (await admin.post("/api/community/reports", json=report_body(post["id"]), headers=csrf(admin))).status_code == 404
    # Public invisibility does not erase the original post, comment, or review evidence.
    async with community.async_session_maker() as db:
        assert (await db.get(CommunityPost, post["id"])).is_hidden
        assert await db.get(CommunityComment, comment_body["id"]) is not None
    history = (await reader.get("/api/community/reports/mine")).json()["items"][0]
    assert history["targetContent"] == post["content"] and history["status"] == "resolved"
    assert (await admin.get("/api/community/admin/reports?status=pending")).json()["total"] == 0
    assert (await admin.get("/api/community/admin/reports?status=resolved")).json()["total"] == 1


@pytest.mark.asyncio
async def test_hiding_comment_updates_counts_and_prevents_idempotent_reappearance(moderation_clients):
    owner, reader, admin = moderation_clients
    post, _ = await publish(owner)
    path = f"/api/community/posts/{post['id']}"
    hidden_body = {"id": str(uuid4()), "content": "需要审核的评论原文"}
    hidden = await reader.post(path + "/comments", json=hidden_body, headers=csrf(reader))
    assert hidden.status_code == 201 and hidden.json()["authorId"]
    visible_body = {"id": str(uuid4()), "content": "保留的正常评论"}
    assert (await owner.post(path + "/comments", json=visible_body, headers=csrf(owner))).status_code == 201
    created, _ = await report(owner, hidden_body["id"], "comment", reason="abuse")
    assert created["postId"] == post["id"] and created["targetContent"] == hidden_body["content"]
    response = await review(admin, created["id"], note="核实评论存在人身攻击")
    assert response.status_code == 200 and response.json()["targetState"] == "hidden"
    comments = (await reader.get(path + "/comments")).json()
    assert comments["total"] == 1 and [item["id"] for item in comments["items"]] == [visible_body["id"]]
    assert (await reader.get(path)).json()["commentCount"] == 1
    assert (await reader.get("/api/community/posts")).json()["items"][0]["commentCount"] == 1
    assert (await reader.post(path + "/comments", json=hidden_body, headers=csrf(reader))).status_code == 404
    async with community.async_session_maker() as db:
        assert (await db.get(CommunityComment, hidden_body["id"])).is_hidden
        assert not (await db.get(CommunityPost, post["id"])).is_hidden


@pytest.mark.asyncio
async def test_dismissal_is_final_and_does_not_hide_content(moderation_clients):
    owner, reader, admin = moderation_clients
    post, _ = await publish(owner)
    created, _ = await report(reader, post["id"], reason="other", details="请核实信息")
    response = await review(admin, created["id"], "dismiss", "经核查属于正常的面试经验分享")
    assert response.status_code == 200, response.text
    dismissed = response.json()
    assert dismissed["status"] == "dismissed" and dismissed["targetState"] == "visible"
    assert (await owner.get(f"/api/community/posts/{post['id']}")).status_code == 200
    for action in ("hide", "dismiss"):
        response = await review(admin, created["id"], action, "第二次处理不能覆盖原审核")
        assert response.status_code == 409, response.text
    history = (await reader.get("/api/community/reports/mine")).json()["items"][0]
    assert history["reviewNote"] == dismissed["reviewNote"] and history["reviewedAt"] == dismissed["reviewedAt"]
    assert (await admin.get("/api/community/admin/reports?status=dismissed")).json()["total"] == 1
    duplicate, _ = await report(reader, post["id"])
    assert duplicate["id"] == created["id"] and duplicate["status"] == "dismissed"


@pytest.mark.asyncio
async def test_report_evidence_survives_edit_and_parent_deletion(moderation_clients):
    owner, reader, admin = moderation_clients
    post, body = await publish(owner)
    path = f"/api/community/posts/{post['id']}"
    comment_body = {"id": str(uuid4()), "content": "随帖子删除仍需保留的举报证据"}
    assert (await reader.post(path + "/comments", json=comment_body, headers=csrf(reader))).status_code == 201
    reported_post, _ = await report(reader, post["id"])
    reported_comment, _ = await report(owner, comment_body["id"], "comment")
    edited = {key: value for key, value in body.items() if key != "id"}
    edited.update(title="修改后的新标题", content="修改后另一份面经正文，不能改变举报时所保留的内容证据。")
    assert (await owner.put(path, json=edited, headers=csrf(owner))).status_code == 200
    snapshot = (await reader.get("/api/community/reports/mine")).json()["items"][0]
    assert snapshot["targetTitle"] == post["title"] and snapshot["targetContent"] == post["content"]
    assert (await owner.delete(path, headers=csrf(owner))).status_code == 204
    # Retained evidence reserves old identifiers so deleted content cannot be resurrected.
    replay = await owner.post("/api/community/posts", json=body, headers=csrf(owner))
    assert replay.status_code == 409, replay.text
    fresh, _ = await publish(owner, title="新的独立面经")
    fresh_comments = f"/api/community/posts/{fresh['id']}/comments"
    reused_comment = await reader.post(fresh_comments, json=comment_body, headers=csrf(reader))
    assert reused_comment.status_code == 409, reused_comment.text
    assert (await reader.get(fresh_comments)).json()["total"] == 0
    assert (await owner.get(path)).status_code == 404
    rows = (await admin.get("/api/community/admin/reports?status=all")).json()["items"]
    assert len(rows) == 2 and all(item["targetState"] == "deleted" for item in rows)
    for item in (reported_post, reported_comment):
        response = await review(admin, item["id"])
        assert response.status_code == 409, response.text
        response = await review(admin, item["id"], "dismiss", "原内容已由作者删除，保留举报记录")
        assert response.status_code == 200 and response.json()["status"] == "dismissed"
    comment_history = (await owner.get("/api/community/reports/mine")).json()["items"][0]
    assert comment_history["targetContent"] == comment_body["content"]


@pytest.mark.asyncio
async def test_reports_are_paginated_and_status_filters_do_not_leak_rows(moderation_clients):
    owner, reader, admin = moderation_clients
    for index in range(21):
        post, _ = await publish(owner, title=f"待审核面经 {index}")
        await report(reader, post["id"])
    first = (await reader.get("/api/community/reports/mine")).json()
    second = (await reader.get("/api/community/reports/mine?page=2")).json()
    assert first["total"] == 21 and first["pageSize"] == 20 and len(first["items"]) == 20
    assert second["page"] == 2 and len(second["items"]) == 1
    assert not {row["id"] for row in first["items"]} & {row["id"] for row in second["items"]}
    response = await review(admin, first["items"][0]["id"], "dismiss", "正常分享")
    assert response.status_code == 200
    pending = (await admin.get("/api/community/admin/reports?status=pending")).json()
    assert pending["total"] == 20 and len(pending["items"]) == 20
    assert all(item["status"] == "pending" for item in pending["items"])
    assert (await owner.get("/api/community/reports/mine")).json()["total"] == 0


@pytest.mark.asyncio
async def test_concurrent_administrators_cannot_replace_a_review(moderation_clients):
    owner, reader, admin = moderation_clients
    async with community.async_session_maker() as db:
        if db.bind.dialect.name != "postgresql":
            pytest.skip("Review row-lock race is verified on real PostgreSQL")
    post, _ = await publish(owner)
    created, _ = await report(reader, post["id"])
    results = await asyncio.gather(review(admin, created["id"], "hide", "并发下架"),
                                   review(admin, created["id"], "dismiss", "并发驳回"))
    assert sorted(response.status_code for response in results) == [200, 409]
    winner = next(response.json() for response in results if response.status_code == 200)
    final = (await reader.get("/api/community/reports/mine")).json()["items"][0]
    assert final["status"] == winner["status"] and final["reviewNote"] == winner["reviewNote"]


def test_moderation_migration_preserves_existing_content_and_backfills_visibility(tmp_path):
    scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "migrations"))
    engine = create_engine(f"sqlite:///{tmp_path / 'pre_moderation.db'}")
    post_id, comment_id, user_id, report_id = (str(uuid4()) for _ in range(4))
    original_post = "已有面经正文，在迁移前发布，升级后仍保持原文并默认可见。"
    original_comment = "已有评论原文"

    def upgrade(connection, revision):
        spec = importlib.util.spec_from_file_location("moderation_backfill_" + revision.revision, revision.path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()

    try:
        with engine.begin() as connection:
            for revision in reversed(list(scripts.walk_revisions(base="base", head="20261026_0011"))):
                upgrade(connection, revision)
            before = MetaData()
            users = Table("platform_users", before, autoload_with=connection)
            posts = Table("platform_community_posts", before, autoload_with=connection)
            comments = Table("platform_community_comments", before, autoload_with=connection)
            connection.execute(users.insert().values(id=user_id, username="existing_author", password_hash="test",
                                                       role="user", created_at=utcnow()))
            connection.execute(posts.insert().values(id=post_id, user_id=user_id, title="已有面经", company="已有公司",
                                                       role="后端工程师", direction="后端开发", stage="校招 · 一面",
                                                       outcome="持续复盘", content=original_post, featured=False,
                                                       created_at=utcnow()))
            connection.execute(comments.insert().values(id=comment_id, post_id=post_id, user_id=user_id,
                                                          content=original_comment, created_at=utcnow()))
            upgrade(connection, scripts.get_revision("20261026_0012"))
            connection.execute(CommunityReport.__table__.insert().values(
                id=report_id, reporter_id=user_id, reporter_name="existing_author", target_type="post",
                target_id=post_id, post_id=post_id, target_title="已有面经", target_content=original_post, reason="other"))
        # Verify committed rows through a new connection, including DB-provided backfills.
        with engine.connect() as connection:
            assert connection.execute(select(CommunityPost.content, CommunityPost.is_hidden)).one() == (original_post, False)
            assert connection.execute(select(CommunityComment.content, CommunityComment.is_hidden)).one() == (original_comment, False)
            report = connection.execute(select(CommunityReport.__table__)).mappings().one()
            assert report["id"] == report_id and report["target_content"] == original_post
            assert report["status"] == "pending"
    finally:
        engine.dispose()
