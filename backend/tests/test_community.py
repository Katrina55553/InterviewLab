"""Database-backed community contracts, cross-account visibility and idempotency."""

import asyncio
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import event, func, select

from app.platform import auth, community
from app.platform.models import CommunityComment, CommunityLike, CommunityTag
from tests.auth_helpers import login_test_user
from tests.platform_db import isolated_database, postgres_test_url


@pytest_asyncio.fixture(params=["sqlite", "postgresql"])
async def community_clients(request, tmp_path, monkeypatch):
    if request.param == "postgresql" and not postgres_test_url():
        pytest.skip("Set PLATFORM_TEST_DATABASE_URL for real PostgreSQL community tests")
    async with isolated_database(request.param, tmp_path) as database:
        if request.param == "sqlite":
            @event.listens_for(database.engine.sync_engine, "connect")
            def enable_foreign_keys(connection, _record):
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys = ON")
                cursor.close()

            await database.engine.dispose()
        monkeypatch.setattr(auth, "async_session_maker", database.sessions)
        monkeypatch.setattr(community, "async_session_maker", database.sessions)
        monkeypatch.setattr(auth, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("test offline")))
        monkeypatch.setattr(auth.settings, "AUTH_COOKIE_SECURE", False)
        monkeypatch.setattr(auth.settings, "ENVIRONMENT", "development")
        monkeypatch.setattr(auth.settings, "CORS_ORIGINS", ["http://localhost:8000"])
        app = FastAPI()
        app.add_middleware(auth.AuthCSRFMiddleware)
        app.include_router(auth.router, prefix="/api")
        app.include_router(community.router, prefix="/api")
        transport = httpx.ASGITransport(app=app)
        async with (
            httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://localhost:8000"}) as first,
            httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Origin": "http://localhost:8000"}) as second,
        ):
            yield first, second


async def register(client, name):
    return await login_test_user(client, name, "community-test-pass")


def csrf(client):
    return {"X-CSRF-Token": client.cookies[auth.CSRF_COOKIE]}


def post_body(**overrides):
    return {"id": str(uuid4()), "title": "Java 面试复盘", "company": "测试公司", "role": "后端工程师",
            "direction": "后端开发", "stage": "校招 · 一面", "outcome": "持续复盘", "tags": ["Java", "项目深挖"],
            "content": "## 复盘\n这次面试围绕系统设计展开，我需要补充具体的指标与验证过程。", **overrides}


async def publish(client, **fields):
    body = post_body(**fields)
    response = await client.post("/api/community/posts", json=body, headers=csrf(client))
    assert response.status_code == 201, response.text
    return response.json(), body


@pytest.mark.asyncio
async def test_requires_login_csrf_and_validates_server_owned_fields(community_clients):
    first, _ = community_clients
    assert (await first.get("/api/community/posts")).status_code == 401
    assert (await first.get("/api/community/meta")).status_code == 401
    assert (await first.get("/api/community/draft")).status_code == 401
    await register(first, "community_owner")
    assert (await first.post("/api/community/posts", json=post_body())).status_code == 403
    assert (await first.post("/api/community/posts", json=post_body(user_id="forged"), headers=csrf(first))).status_code == 422
    assert (await first.post("/api/community/posts", json=post_body(content=" " * 25), headers=csrf(first))).status_code == 422
    assert (await first.post("/api/community/posts", json=post_body(tags=["x" * 33]), headers=csrf(first))).status_code == 422
    missing = str(uuid4())
    assert (await first.get(f"/api/community/posts/{missing}")).status_code == 404
    assert (await first.put(f"/api/community/posts/{missing}/like", headers=csrf(first))).status_code == 404
    assert (await first.get("/api/community/posts?direction=invalid")).status_code == 422


@pytest.mark.asyncio
async def test_shared_posts_private_drafts_and_one_idempotent_like_relation(community_clients):
    first, second = community_clients
    owner = await register(first, "community_owner")
    await register(second, "community_reader")
    post, _ = await publish(first)
    path = f"/api/community/posts/{post['id']}"
    assert post["authorId"] == owner["id"] and post["likes"] == 0
    assert "password" not in str(post)
    feed = (await second.get("/api/community/posts")).json()
    assert feed["total"] == 1 and feed["items"][0]["author"] == "community_owner"
    assert "content" not in feed["items"][0]
    assert (await second.get(path)).json()["content"] == post["content"]
    results = await asyncio.gather(*(second.put(path + "/like", headers=csrf(second)) for _ in range(3)))
    assert all(result.status_code == 200 for result in results)
    assert (await second.get(path)).json()["likes"] == 1
    assert (await second.get(path)).json()["liked"] is True
    assert (await first.get(path)).json()["liked"] is False
    assert (await second.get("/api/community/posts?tab=liked")).json()["total"] == 1
    assert (await first.put(path + "/like", headers=csrf(first))).json()["likes"] == 2
    for _ in range(2):
        assert (await second.delete(path + "/like", headers=csrf(second))).json() == {"liked": False, "likes": 1}
    assert (await second.get("/api/community/posts?tab=liked")).json()["total"] == 0
    assert (await first.get("/api/community/meta")).json()["myLikes"] == 1
    assert (await second.get("/api/community/meta")).json()["myPosts"] == 0
    assert (await first.get("/api/community/posts?tab=mine")).json()["total"] == 1
    assert (await second.get("/api/community/posts?tab=mine")).json()["total"] == 0


@pytest.mark.asyncio
async def test_server_draft_versioning_and_publish_replay(community_clients):
    first, second = community_clients
    await register(first, "community_owner")
    await register(second, "community_reader")
    body = post_body()
    draft = {**body, "tags": "Java 项目深挖", "version": 0}
    saved = await first.put("/api/community/draft", json=draft, headers=csrf(first))
    assert saved.status_code == 200 and saved.json()["version"] == 1
    assert (await second.get("/api/community/draft")).json() == {"draft": None, "version": 0}
    assert (await first.get("/api/community/posts")).json()["total"] == 0
    assert (await first.put("/api/community/draft", json=draft, headers=csrf(first))).json()["version"] == 1
    assert (await first.put("/api/community/draft", json={**draft, "title": "stale update"}, headers=csrf(first))).status_code == 409
    assert (await first.get("/api/community/draft")).json()["draft"]["content"] == body["content"]
    body["draftVersion"] = 1
    created = await first.post("/api/community/posts", json=body, headers=csrf(first))
    assert created.status_code == 201, created.text
    assert (await first.get("/api/community/draft")).json()["draft"] is None
    replay = await first.post("/api/community/posts", json=body, headers=csrf(first))
    assert replay.status_code == 200 and replay.json()["id"] == created.json()["id"]
    assert (await first.post("/api/community/posts", json={**body, "title": "changed"}, headers=csrf(first))).status_code == 409
    assert (await second.post("/api/community/posts", json=body, headers=csrf(second))).status_code == 409
    assert (await first.get("/api/community/posts")).json()["total"] == 1


@pytest.mark.asyncio
async def test_comments_are_shared_validated_paginated_and_replayed(community_clients):
    first, second = community_clients
    await register(first, "community_owner")
    await register(second, "community_reader")
    post, _ = await publish(first)
    path = f"/api/community/posts/{post['id']}/comments"
    body = {"id": str(uuid4()), "content": "<script>example</script> 这段内容应以文本展示"}
    for expected in (201, 200):
        response = await second.post(path, json=body, headers=csrf(second))
        assert response.status_code == expected and response.json()["author"] == "community_reader"
    assert (await first.post(path, json=body, headers=csrf(first))).status_code == 409
    assert (await second.post(path, json={"id": str(uuid4()), "content": "   "}, headers=csrf(second))).status_code == 422
    for index in range(30):
        response = await second.post(path, json={"id": str(uuid4()), "content": f"补充内容 {index}"}, headers=csrf(second))
        assert response.status_code == 201
    page = (await first.get(path)).json()
    assert page["total"] == 31 and len(page["items"]) == 30 and page["hasMore"]
    assert page["items"][0]["content"] == body["content"]
    assert len((await first.get(path + "?page=2")).json()["items"]) == 1
    assert (await first.get(f"/api/community/posts/{post['id']}")).json()["commentCount"] == 31


@pytest.mark.asyncio
async def test_filters_pagination_ranking_and_literal_search(community_clients):
    first, _ = community_clients
    await register(first, "community_owner")
    posts = []
    for index in range(7):
        post, _ = await publish(first, title=f"面试 {index}", tags=["Java"] if index < 2 else ["JavaScript"],
                                company="测试公司" if index < 2 else "其他公司")
        posts.append(post)
    assert (await first.put(f"/api/community/posts/{posts[0]['id']}/like", headers=csrf(first))).status_code == 200
    assert (await first.get("/api/community/posts")).json()["items"][0]["id"] == posts[0]["id"]
    assert (await first.get("/api/community/posts?tab=latest")).json()["items"][0]["id"] == posts[-1]["id"]
    assert (await first.get("/api/community/posts?page=900")).json()["page"] == 2
    assert len((await first.get("/api/community/posts?page=2")).json()["items"]) == 2
    assert (await first.get("/api/community/posts?topic=Java")).json()["total"] == 2
    assert (await first.get("/api/community/posts", params={"q": "%_"})).json()["total"] == 0
    assert (await first.get("/api/community/posts", params={"company": "测试公司", "q": "面试 Java"})).json()["total"] == 2
    meta = (await first.get("/api/community/meta")).json()
    assert meta["myPosts"] == 7 and meta["topics"][0] == {"tag": "JavaScript", "count": 5}


@pytest.mark.asyncio
async def test_edit_and_delete_require_owner_login_and_csrf(community_clients):
    first, second = community_clients
    await register(first, "community_owner")
    post, body = await publish(first)
    path = f"/api/community/posts/{post['id']}"
    fields = {key: value for key, value in body.items() if key != "id"}
    assert (await second.put(path, json=fields)).status_code == 401
    assert (await second.delete(path)).status_code == 401
    assert (await first.put(path, json=fields)).status_code == 403
    assert (await first.delete(path)).status_code == 403
    await register(second, "community_reader")
    assert (await second.put(path, json=fields, headers=csrf(second))).status_code == 403
    assert (await second.delete(path, headers=csrf(second))).status_code == 403
    assert (await first.get(path)).json() == post
    missing = f"/api/community/posts/{uuid4()}"
    assert (await first.put(missing, json=fields, headers=csrf(first))).status_code == 404
    assert (await first.delete(missing, headers=csrf(first))).status_code == 404


@pytest.mark.asyncio
async def test_edit_preserves_identity_engagement_and_private_draft(community_clients):
    first, second = community_clients
    await register(first, "community_owner")
    await register(second, "community_reader")
    post, body = await publish(first)
    path = f"/api/community/posts/{post['id']}"
    fields = {key: value for key, value in body.items() if key != "id"}
    await second.put(path + "/like", headers=csrf(second))
    comment = {"id": str(uuid4()), "content": "原有评论需要保留"}
    await second.post(path + "/comments", json=comment, headers=csrf(second))
    draft = {**post_body(title="另一篇未发布面经"), "tags": "草稿", "version": 0}
    saved = (await first.put("/api/community/draft", json=draft, headers=csrf(first))).json()
    fields.update(title="  修改后的面试复盘  ", company="新公司", role="前端工程师", direction="前端开发",
                  stage="社招 · 终面", outcome="已拿 Offer", tags=["React", "React", "新话题"],
                  content="## 更新复盘\n补充前端面试的具体题目、回答过程，以及后续需要改进的准备方向。")
    for _ in range(2):
        response = await first.put(path, json=fields, headers=csrf(first))
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["title"] == fields["title"].strip()
        for key in ("company", "role", "direction", "stage", "outcome", "content"):
            assert updated[key] == fields[key]
        assert set(updated["tags"]) == {"React", "新话题"}
        for key in ("id", "authorId", "author", "createdAt", "featured"):
            assert updated[key] == post[key]
        assert updated["likes"] == 1 and updated["commentCount"] == 1 and not updated["liked"]
    assert (await second.get(path)).json()["liked"] is True
    assert (await second.get(path)).json()["content"] == fields["content"]
    assert (await second.get(path + "/comments")).json()["items"][0]["content"] == comment["content"]
    assert (await first.get("/api/community/draft")).json() == saved
    assert (await second.get("/api/community/posts?topic=Java")).json()["total"] == 0
    feed = (await second.get("/api/community/posts", params={"q": "修改后", "topic": "React"})).json()
    assert feed["total"] == 1 and feed["items"][0]["title"] == fields["title"].strip()
    assert (await first.get("/api/community/meta")).json()["companies"] == ["新公司"]


@pytest.mark.asyncio
async def test_invalid_edits_leave_post_and_tags_unchanged(community_clients):
    first, _ = community_clients
    await register(first, "community_owner")
    post, body = await publish(first)
    path = f"/api/community/posts/{post['id']}"
    fields = {key: value for key, value in body.items() if key != "id"}
    for invalid in ({"title": "  "}, {"content": "太短"}, {"company": ""}, {"stage": " "},
                    {"direction": "invalid"}, {"outcome": "invalid"}, {"tags": ["x" * 33]},
                    {"tags": [str(index) for index in range(6)]}, {"authorId": "forged"},
                    {"featured": True}, {"id": str(uuid4())}, {"draftVersion": 1}):
        response = await first.put(path, json={**fields, **invalid}, headers=csrf(first))
        assert response.status_code == 422, response.text
        assert (await first.get(path)).json() == post
    response = await first.put(path, json={**fields, "tags": []}, headers=csrf(first))
    assert response.status_code == 200 and response.json()["tags"] == []


@pytest.mark.asyncio
async def test_delete_cascades_engagement_and_refreshes_feeds_and_counts(community_clients):
    first, second = community_clients
    await register(first, "community_owner")
    await register(second, "community_reader")
    post, _ = await publish(first)
    other, _ = await publish(second, company="保留公司", tags=["保留话题"])
    path = f"/api/community/posts/{post['id']}"
    await second.put(path + "/like", headers=csrf(second))
    await second.post(path + "/comments", json={"id": str(uuid4()), "content": "随帖子删除"}, headers=csrf(second))
    response = await first.delete(path, headers=csrf(first))
    assert response.status_code == 204 and not response.content
    assert (await second.get(path)).status_code == 404
    assert (await second.get(path + "/comments")).status_code == 404
    assert (await second.put(path + "/like", headers=csrf(second))).status_code == 404
    assert (await second.post(path + "/comments", json={"id": str(uuid4()), "content": "不能添加"}, headers=csrf(second))).status_code == 404
    assert (await first.delete(path, headers=csrf(first))).status_code == 404
    assert (await first.get("/api/community/posts?tab=mine")).json()["total"] == 0
    assert (await second.get("/api/community/posts?tab=liked")).json()["total"] == 0
    assert (await first.get("/api/community/meta")).json() == {
        "companies": ["保留公司"], "topics": [{"tag": "保留话题", "count": 1}], "myPosts": 0, "myLikes": 0,
    }
    assert (await second.get("/api/community/meta")).json()["myLikes"] == 0
    assert (await second.get(f"/api/community/posts/{other['id']}")).status_code == 200
    async with community.async_session_maker() as db:
        for model in (CommunityTag, CommunityLike, CommunityComment):
            assert await db.scalar(select(func.count()).select_from(model).where(model.post_id == post["id"])) == 0
