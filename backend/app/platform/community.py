"""Authenticated community persistence. PostgreSQL owns posts, likes and drafts."""

import re
from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.platform.auth import current_user
from app.platform.models import (
    CommunityComment,
    CommunityDraft,
    CommunityLike,
    CommunityPost,
    CommunityTag,
    User,
    utcnow,
)

router = APIRouter(prefix="/community", tags=["platform-community"])
Direction = Literal["前端开发", "后端开发", "算法 / AI", "产品 / 设计", "测试开发"]
Outcome = Literal["已拿 Offer", "进入下一轮", "持续复盘"]
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]


class PostFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=100)
    company: str = Field(min_length=1, max_length=50)
    role: str = Field(min_length=1, max_length=60)
    direction: Direction
    stage: str = Field(min_length=1, max_length=32)
    outcome: Outcome
    tags: list[Tag] = Field(max_length=5)
    content: str = Field(min_length=20, max_length=20000)

    @field_validator("tags")
    @classmethod
    def unique_tags(cls, value):
        return list(dict.fromkeys(value))


class PostInput(PostFields):
    id: UUID
    draftVersion: int = Field(default=0, ge=0)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    title: str = Field(default="", max_length=100)
    company: str = Field(default="", max_length=50)
    role: str = Field(default="", max_length=60)
    direction: Direction = "前端开发"
    stage: str = Field(default="校招 · 一面", max_length=32)
    outcome: Outcome = "持续复盘"
    tags: str = Field(default="", max_length=100)
    content: str = Field(default="", max_length=20000)
    version: int = Field(ge=0)


class CommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    content: str = Field(min_length=1, max_length=2000)


def iso(value):
    return value.replace(tzinfo=UTC).isoformat()


def post_query(user_id):
    likes = select(CommunityLike.post_id, func.count().label("total")).group_by(CommunityLike.post_id).subquery()
    comments = select(CommunityComment.post_id, func.count().label("total")).group_by(CommunityComment.post_id).subquery()
    liked = exists().where(CommunityLike.post_id == CommunityPost.id, CommunityLike.user_id == user_id)
    return select(CommunityPost, User.username, func.coalesce(likes.c.total, 0).label("like_count"), func.coalesce(comments.c.total, 0), liked).join(
        User, User.id == CommunityPost.user_id
    ).outerjoin(likes, likes.c.post_id == CommunityPost.id).outerjoin(comments, comments.c.post_id == CommunityPost.id)


async def post_views(db, rows, *, full=False):
    ids = [row[0].id for row in rows]
    tags = {}
    if ids:
        for post_id, tag in await db.execute(select(CommunityTag.post_id, CommunityTag.tag).where(CommunityTag.post_id.in_(ids)).order_by(CommunityTag.tag)):
            tags.setdefault(post_id, []).append(tag)
    result = []
    for post, author, likes, comments, liked in rows:
        item = {"id": post.id, "authorId": post.user_id, "author": author, "title": post.title, "company": post.company,
                "role": post.role, "direction": post.direction, "stage": post.stage, "outcome": post.outcome,
                "tags": tags.get(post.id, []), "summary": re.sub(r"\s+", " ", re.sub(r"[#*`>]", "", post.content)).strip()[:150],
                "likes": likes, "liked": liked, "commentCount": comments, "featured": post.featured, "createdAt": iso(post.created_at)}
        if full:
            item["content"] = post.content
        result.append(item)
    return result


async def require_post(db, post_id):
    post = await db.get(CommunityPost, str(post_id))
    if post is None:
        raise HTTPException(404, "面经不存在或已删除")
    return post


async def require_owned_post(db, post_id, user_id):
    post = await db.scalar(select(CommunityPost).where(CommunityPost.id == str(post_id)).with_for_update())
    if post is None:
        raise HTTPException(404, "面经不存在或已删除")
    if post.user_id != user_id:
        raise HTTPException(403, "只能编辑或删除自己发布的面经")
    return post


@router.get("/posts")
async def list_posts(
    q: str = Query(default="", max_length=200), direction: Direction | None = None,
    company: str = Query(default="", max_length=50), topic: str = Query(default="", max_length=32),
    tab: Literal["recommended", "latest", "liked", "mine"] = "recommended",
    page: int = Query(default=1, ge=1, le=100000), page_size: int = Query(default=5, ge=1, le=50),
    user: User = Depends(current_user),
):
    conditions = []
    for word in q.lower().split():
        conditions.append(or_(*(func.lower(field).contains(word, autoescape=True) for field in (
            CommunityPost.title, CommunityPost.company, CommunityPost.role, CommunityPost.content)),
            exists().where(CommunityTag.post_id == CommunityPost.id, func.lower(CommunityTag.tag).contains(word, autoescape=True))))
    if direction:
        conditions.append(CommunityPost.direction == direction)
    if company:
        conditions.append(CommunityPost.company == company)
    if topic:
        conditions.append(exists().where(CommunityTag.post_id == CommunityPost.id, CommunityTag.tag == topic))
    if tab == "mine":
        conditions.append(CommunityPost.user_id == user.id)
    if tab == "liked":
        conditions.append(exists().where(CommunityLike.post_id == CommunityPost.id, CommunityLike.user_id == user.id))
    async with async_session_maker() as db:
        total = await db.scalar(select(func.count()).select_from(CommunityPost).where(*conditions))
        page = min(page, max(1, (total + page_size - 1) // page_size))
        statement = post_query(user.id).where(*conditions)
        if tab == "recommended":
            statement = statement.order_by(CommunityPost.featured.desc(), statement.selected_columns.like_count.desc())
        rows = (await db.execute(statement.order_by(CommunityPost.created_at.desc(), CommunityPost.id.desc())
                                 .offset((page - 1) * page_size).limit(page_size))).all()
        return {"items": await post_views(db, rows), "total": total, "page": page, "pageSize": page_size}


@router.get("/meta")
async def metadata(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        companies = list(await db.scalars(select(CommunityPost.company).distinct().order_by(CommunityPost.company)))
        topics = await db.execute(select(CommunityTag.tag, func.count().label("total")).group_by(CommunityTag.tag)
                                  .order_by(func.count().desc(), CommunityTag.tag).limit(6))
        return {"companies": companies, "topics": [{"tag": tag, "count": count} for tag, count in topics],
                "myPosts": await db.scalar(select(func.count()).select_from(CommunityPost).where(CommunityPost.user_id == user.id)),
                "myLikes": await db.scalar(select(func.count()).select_from(CommunityLike).where(CommunityLike.user_id == user.id))}


@router.get("/draft")
async def get_draft(user: User = Depends(current_user)):
    async with async_session_maker() as db:
        draft = await db.get(CommunityDraft, user.id)
        return {"draft": draft.data if draft else None, "version": draft.version if draft else 0}


@router.put("/draft")
async def save_draft(body: DraftInput, user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        draft = await db.scalar(select(CommunityDraft).where(CommunityDraft.user_id == user.id).with_for_update())
        data = body.model_dump(mode="json", exclude={"version"})
        if draft and draft.data == data and body.version == draft.version - 1:
            return {"draft": draft.data, "version": draft.version}
        if body.version != (draft.version if draft else 0):
            raise HTTPException(409, "草稿已在其他页面更新，请重新打开社区后再编辑")
        if draft:
            draft.data = data
            draft.post_id = str(body.id)
            draft.version += 1
            draft.updated_at = utcnow()
        else:
            draft = CommunityDraft(user_id=user.id, post_id=str(body.id), data=data, version=1)
            db.add(draft)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise HTTPException(409, "草稿已在其他页面更新，请重新打开社区后再编辑") from exc
        return {"draft": data, "version": draft.version}


@router.post("/posts", status_code=201)
async def publish_post(body: PostInput, response: Response, user: User = Depends(current_user)):
    fields = body.model_dump(exclude={"id", "tags", "draftVersion"})
    async with async_session_maker() as db:
        existing = await db.get(CommunityPost, str(body.id))
        if existing:
            existing_tags = set(await db.scalars(select(CommunityTag.tag).where(CommunityTag.post_id == existing.id)))
            if existing.user_id != user.id or any(getattr(existing, key) != value for key, value in fields.items()) or existing_tags != set(body.tags):
                raise HTTPException(409, "发布请求已使用，请刷新社区查看已发布的面经")
            response.status_code = 200
        else:
            db.add(CommunityPost(id=str(body.id), user_id=user.id, **fields))
            try:
                await db.flush()
                db.add_all(CommunityTag(post_id=str(body.id), tag=tag) for tag in body.tags)
                await db.execute(delete(CommunityDraft).where(CommunityDraft.user_id == user.id,
                    CommunityDraft.post_id == str(body.id), CommunityDraft.version == body.draftVersion))
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise HTTPException(409, "发布正在处理中，请使用同一内容重试") from exc
        return (await post_views(db, (await db.execute(post_query(user.id).where(CommunityPost.id == str(body.id)))).all(), full=True))[0]


@router.get("/posts/{post_id}")
async def get_post(post_id: UUID, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        rows = (await db.execute(post_query(user.id).where(CommunityPost.id == str(post_id)))).all()
        if not rows:
            raise HTTPException(404, "面经不存在或已删除")
        return (await post_views(db, rows, full=True))[0]


@router.put("/posts/{post_id}")
async def edit_post(post_id: UUID, body: PostFields, user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        post = await require_owned_post(db, post_id, user.id)
        for key, value in body.model_dump(exclude={"tags"}).items():
            setattr(post, key, value)
        await db.execute(delete(CommunityTag).where(CommunityTag.post_id == post.id))
        db.add_all(CommunityTag(post_id=post.id, tag=tag) for tag in body.tags)
        await db.flush()
        rows = (await db.execute(post_query(user.id).where(CommunityPost.id == post.id))).all()
        return (await post_views(db, rows, full=True))[0]


@router.delete("/posts/{post_id}", status_code=204)
async def delete_post(post_id: UUID, user: User = Depends(current_user)):
    async with async_session_maker() as db, db.begin():
        post = await require_owned_post(db, post_id, user.id)
        await db.delete(post)
    return Response(status_code=204)


@router.put("/posts/{post_id}/like")
async def like_post(post_id: UUID, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id)
        if db.bind.dialect.name == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        await db.execute(insert(CommunityLike).values(post_id=str(post_id), user_id=user.id, created_at=utcnow())
                         .on_conflict_do_nothing(index_elements=["post_id", "user_id"]))
        await db.commit()
        return {"liked": True, "likes": await db.scalar(select(func.count()).select_from(CommunityLike).where(CommunityLike.post_id == str(post_id)))}


@router.delete("/posts/{post_id}/like")
async def unlike_post(post_id: UUID, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id)
        await db.execute(delete(CommunityLike).where(CommunityLike.post_id == str(post_id), CommunityLike.user_id == user.id))
        await db.commit()
        return {"liked": False, "likes": await db.scalar(select(func.count()).select_from(CommunityLike).where(CommunityLike.post_id == str(post_id)))}


@router.get("/posts/{post_id}/comments")
async def list_comments(post_id: UUID, page: int = Query(default=1, ge=1, le=100000), user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id)
        total = await db.scalar(select(func.count()).select_from(CommunityComment).where(CommunityComment.post_id == str(post_id)))
        rows = await db.execute(select(CommunityComment, User.username).join(User, User.id == CommunityComment.user_id)
                                .where(CommunityComment.post_id == str(post_id)).order_by(CommunityComment.created_at, CommunityComment.id)
                                .offset((page - 1) * 30).limit(30))
        return {"items": [{"id": row.id, "author": author, "content": row.content, "createdAt": iso(row.created_at)} for row, author in rows],
                "total": total, "page": page, "hasMore": page * 30 < total}


@router.post("/posts/{post_id}/comments", status_code=201)
async def add_comment(post_id: UUID, body: CommentInput, response: Response, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id)
        row = await db.get(CommunityComment, str(body.id))
        if row:
            if row.user_id != user.id or row.post_id != str(post_id) or row.content != body.content:
                raise HTTPException(409, "评论请求已使用")
            response.status_code = 200
        else:
            row = CommunityComment(id=str(body.id), user_id=user.id, post_id=str(post_id), content=body.content)
            db.add(row)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise HTTPException(409, "评论正在处理中，请使用同一内容重试") from exc
        return {"id": row.id, "author": user.username, "content": row.content, "createdAt": iso(row.created_at)}
