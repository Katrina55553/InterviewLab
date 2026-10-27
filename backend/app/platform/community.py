"""Authenticated community persistence. PostgreSQL owns posts, likes and drafts."""

import re
from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.core.database import async_session_maker
from app.platform.auth import current_user, require_admin
from app.platform.models import (
    CommunityComment,
    CommunityDraft,
    CommunityLike,
    CommunityPost,
    CommunityReport,
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


class ReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: UUID
    targetType: Literal["post", "comment"]
    targetId: UUID
    reason: Literal["spam", "abuse", "privacy", "illegal", "other"]
    details: str = Field(default="", max_length=1000)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["hide", "dismiss"]
    note: str = Field(min_length=1, max_length=1000)


def iso(value):
    return value.replace(tzinfo=UTC).isoformat()


def post_query(user_id):
    likes = select(CommunityLike.post_id, func.count().label("total")).group_by(CommunityLike.post_id).subquery()
    comments = select(CommunityComment.post_id, func.count().label("total")).where(
        CommunityComment.is_hidden.is_(False)).group_by(CommunityComment.post_id).subquery()
    liked = exists().where(CommunityLike.post_id == CommunityPost.id, CommunityLike.user_id == user_id)
    return select(CommunityPost, User.username, func.coalesce(likes.c.total, 0).label("like_count"), func.coalesce(comments.c.total, 0), liked).join(
        User, User.id == CommunityPost.user_id
    ).outerjoin(likes, likes.c.post_id == CommunityPost.id).outerjoin(comments, comments.c.post_id == CommunityPost.id).where(
        CommunityPost.is_hidden.is_(False))


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


async def require_post(db, post_id, *, lock=False):
    statement = select(CommunityPost).where(CommunityPost.id == str(post_id))
    if lock:
        statement = statement.with_for_update()
    post = await db.scalar(statement)
    if post is None or post.is_hidden:
        raise HTTPException(404, "面经不存在、已删除或已下架")
    return post


async def require_owned_post(db, post_id, user_id):
    post = await db.scalar(select(CommunityPost).where(CommunityPost.id == str(post_id)).with_for_update())
    if post is None or post.is_hidden:
        raise HTTPException(404, "面经不存在、已删除或已下架")
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
    conditions = [CommunityPost.is_hidden.is_(False)]
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
        companies = list(await db.scalars(select(CommunityPost.company).where(CommunityPost.is_hidden.is_(False))
                                         .distinct().order_by(CommunityPost.company)))
        topics = await db.execute(select(CommunityTag.tag, func.count().label("total"))
                                  .join(CommunityPost, CommunityPost.id == CommunityTag.post_id)
                                  .where(CommunityPost.is_hidden.is_(False)).group_by(CommunityTag.tag)
                                  .order_by(func.count().desc(), CommunityTag.tag).limit(6))
        return {"companies": companies, "topics": [{"tag": tag, "count": count} for tag, count in topics],
                "myPosts": await db.scalar(select(func.count()).select_from(CommunityPost).where(
                    CommunityPost.user_id == user.id, CommunityPost.is_hidden.is_(False))),
                "myLikes": await db.scalar(select(func.count()).select_from(CommunityLike)
                    .join(CommunityPost, CommunityPost.id == CommunityLike.post_id)
                    .where(CommunityLike.user_id == user.id, CommunityPost.is_hidden.is_(False)))}


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
            if existing.is_hidden:
                raise HTTPException(404, "面经不存在、已删除或已下架")
            existing_tags = set(await db.scalars(select(CommunityTag.tag).where(CommunityTag.post_id == existing.id)))
            if existing.user_id != user.id or any(getattr(existing, key) != value for key, value in fields.items()) or existing_tags != set(body.tags):
                raise HTTPException(409, "发布请求已使用，请刷新社区查看已发布的面经")
            response.status_code = 200
        else:
            if await db.scalar(select(exists().where(CommunityReport.post_id == str(body.id)))):
                raise HTTPException(409, "该面经编号已有举报记录，请使用新的发布请求")
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
        rows = (await db.execute(post_query(user.id).where(CommunityPost.id == str(body.id)))).all()
        if not rows:
            raise HTTPException(404, "面经不存在、已删除或已下架")
        return (await post_views(db, rows, full=True))[0]


@router.get("/posts/{post_id}")
async def get_post(post_id: UUID, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        rows = (await db.execute(post_query(user.id).where(CommunityPost.id == str(post_id)))).all()
        if not rows:
            raise HTTPException(404, "面经不存在、已删除或已下架")
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
        await require_post(db, post_id, lock=True)
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
        await require_post(db, post_id, lock=True)
        await db.execute(delete(CommunityLike).where(CommunityLike.post_id == str(post_id), CommunityLike.user_id == user.id))
        await db.commit()
        return {"liked": False, "likes": await db.scalar(select(func.count()).select_from(CommunityLike).where(CommunityLike.post_id == str(post_id)))}


@router.get("/posts/{post_id}/comments")
async def list_comments(post_id: UUID, page: int = Query(default=1, ge=1, le=100000), user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id)
        total = await db.scalar(select(func.count()).select_from(CommunityComment).where(
            CommunityComment.post_id == str(post_id), CommunityComment.is_hidden.is_(False)))
        rows = await db.execute(select(CommunityComment, User.username).join(User, User.id == CommunityComment.user_id)
                                .where(CommunityComment.post_id == str(post_id), CommunityComment.is_hidden.is_(False))
                                .order_by(CommunityComment.created_at, CommunityComment.id)
                                .offset((page - 1) * 30).limit(30))
        return {"items": [{"id": row.id, "authorId": row.user_id, "author": author, "content": row.content,
                           "createdAt": iso(row.created_at)} for row, author in rows],
                "total": total, "page": page, "hasMore": page * 30 < total}


@router.post("/posts/{post_id}/comments", status_code=201)
async def add_comment(post_id: UUID, body: CommentInput, response: Response, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        await require_post(db, post_id, lock=True)
        row = await db.get(CommunityComment, str(body.id))
        if row:
            if row.is_hidden:
                raise HTTPException(404, "评论不存在、已删除或已下架")
            if row.user_id != user.id or row.post_id != str(post_id) or row.content != body.content:
                raise HTTPException(409, "评论请求已使用")
            response.status_code = 200
        else:
            if await db.scalar(select(exists().where(
                CommunityReport.target_type == "comment", CommunityReport.target_id == str(body.id)
            ))):
                raise HTTPException(409, "该评论编号已有举报记录，请使用新的评论请求")
            row = CommunityComment(id=str(body.id), user_id=user.id, post_id=str(post_id), content=body.content)
            db.add(row)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise HTTPException(409, "评论正在处理中，请使用同一内容重试") from exc
        return {"id": row.id, "authorId": row.user_id, "author": user.username, "content": row.content,
                "createdAt": iso(row.created_at)}


async def report_views(db, reports):
    """Keep evidence immutable, but always show the target's current availability."""
    post_ids = {report.post_id for report in reports}
    comment_ids = {report.target_id for report in reports if report.target_type == "comment"}
    posts = {row.id: row for row in await db.scalars(select(CommunityPost).where(CommunityPost.id.in_(post_ids)))} if post_ids else {}
    comments = {row.id: row for row in await db.scalars(select(CommunityComment).where(CommunityComment.id.in_(comment_ids)))} if comment_ids else {}
    items = []
    for report in reports:
        post = posts.get(report.post_id)
        target = post if report.target_type == "post" else comments.get(report.target_id)
        state = "deleted" if post is None or target is None else "hidden" if post.is_hidden or target.is_hidden else "visible"
        items.append({"id": report.id, "targetType": report.target_type, "targetId": report.target_id,
                      "postId": report.post_id, "reason": report.reason, "details": report.details, "status": report.status,
                      "createdAt": iso(report.created_at), "reviewedAt": iso(report.reviewed_at) if report.reviewed_at else None,
                      "reviewNote": report.review_note, "targetTitle": report.target_title, "targetContent": report.target_content,
                      "reporter": report.reporter_name, "reviewer": report.reviewer_name, "targetState": state})
    return items


def check_report_request(report, body, user):
    if (report.reporter_id != user.id or report.target_type != body.targetType or report.target_id != str(body.targetId)
            or report.reason != body.reason or report.details != body.details):
        raise HTTPException(409, "举报请求编号已使用，请重新提交")


async def existing_report(db, body, user):
    by_id = await db.get(CommunityReport, str(body.id))
    if by_id:
        check_report_request(by_id, body, user)
        return by_id
    return await db.scalar(select(CommunityReport).where(
        CommunityReport.reporter_id == user.id, CommunityReport.target_type == body.targetType,
        CommunityReport.target_id == str(body.targetId)))


@router.post("/reports", status_code=201)
async def submit_report(body: ReportInput, response: Response, user: User = Depends(current_user)):
    async with async_session_maker() as db:
        duplicate = await existing_report(db, body, user)
        if duplicate:
            response.status_code = 200
            return (await report_views(db, [duplicate]))[0]
        if body.targetType == "post":
            post = await require_post(db, body.targetId, lock=True)
            target = post
        else:
            comment = await db.get(CommunityComment, str(body.targetId))
            if comment is None:
                raise HTTPException(404, "评论不存在、已删除或已下架")
            # All content mutations lock the parent first, including moderation.
            post = await require_post(db, comment.post_id, lock=True)
            target = await db.scalar(select(CommunityComment).where(
                CommunityComment.id == str(body.targetId)).with_for_update().execution_options(populate_existing=True))
            if target is None or target.is_hidden:
                raise HTTPException(404, "评论不存在、已删除或已下架")
        report = CommunityReport(id=str(body.id), reporter_id=user.id, reporter_name=user.username,
                                 target_type=body.targetType, target_id=str(body.targetId), post_id=post.id,
                                 target_title=post.title, target_content=target.content, reason=body.reason, details=body.details)
        db.add(report)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            duplicate = await existing_report(db, body, user)
            if duplicate is None:
                raise HTTPException(409, "举报正在处理中，请使用同一内容重试") from exc
            response.status_code = 200
            report = duplicate
        return (await report_views(db, [report]))[0]


async def report_page(db, conditions, page):
    page_size = 20
    total = await db.scalar(select(func.count()).select_from(CommunityReport).where(*conditions))
    page = min(page, max(1, (total + page_size - 1) // page_size))
    reports = list(await db.scalars(select(CommunityReport).where(*conditions)
                                    .order_by(CommunityReport.created_at.desc(), CommunityReport.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)))
    return {"items": await report_views(db, reports), "total": total, "page": page, "pageSize": page_size}


@router.get("/reports/mine")
async def my_reports(page: int = Query(default=1, ge=1, le=100000), user: User = Depends(current_user)):
    async with async_session_maker() as db:
        return await report_page(db, [CommunityReport.reporter_id == user.id], page)


@router.get("/admin/reports")
async def moderation_reports(
    status: Literal["pending", "resolved", "dismissed", "all"] = "pending",
    page: int = Query(default=1, ge=1, le=100000), user: User = Depends(require_admin),
):
    async with async_session_maker() as db:
        return await report_page(db, [] if status == "all" else [CommunityReport.status == status], page)


@router.post("/admin/reports/{report_id}/review")
async def review_report(report_id: UUID, body: ReviewInput, user: User = Depends(require_admin)):
    async with async_session_maker() as db, db.begin():
        report = await db.get(CommunityReport, str(report_id))
        if report is None:
            raise HTTPException(404, "举报记录不存在")
        if report.status != "pending":
            raise HTTPException(409, "该举报已经处理，请刷新审核列表")
        target = None
        if body.action == "hide":
            post = await db.scalar(select(CommunityPost).where(CommunityPost.id == report.post_id).with_for_update())
            target = post if report.target_type == "post" else await db.scalar(select(CommunityComment).where(
                CommunityComment.id == report.target_id).with_for_update())
            if post is None or target is None:
                raise HTTPException(409, "举报内容已删除，可驳回并记录处理说明")
        # Match submission/author lock order: parent post, comment, then report.
        report = await db.scalar(select(CommunityReport).where(CommunityReport.id == str(report_id))
                                 .with_for_update().execution_options(populate_existing=True))
        if report.status != "pending":
            raise HTTPException(409, "该举报已经处理，请刷新审核列表")
        if target is not None:
            target.is_hidden = True
            await db.flush()
        # The conditional write also protects dialects where FOR UPDATE is unavailable.
        result = await db.execute(update(CommunityReport).where(
            CommunityReport.id == report.id, CommunityReport.status == "pending").values(
                status="resolved" if body.action == "hide" else "dismissed", reviewer_id=user.id,
                reviewer_name=user.username, reviewed_at=utcnow(), review_note=body.note)
                .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise HTTPException(409, "该举报已经处理，请刷新审核列表")
        await db.refresh(report)
        return (await report_views(db, [report]))[0]
