from datetime import datetime, timezone
import os
import shutil
import uuid
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func
from starlette import status
from app.database import SessionLocal
from app.models import Posts, Comments, CommentReactions, Users
from app.routes.auth import get_curent_user

router = APIRouter(prefix="/comments", tags=["comments"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict, Depends(get_curent_user)]


def save_image(image: UploadFile) -> str:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image type")
    file_extension = image.filename.split(".")[-1] if image.filename else "png"
    unique_filename = f"{uuid.uuid4()}.{file_extension}"
    file_path = os.path.join("uploads", "images", unique_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)
    return file_path


def delete_image_file(image_path: Optional[str]):
    if not image_path:
        return
    old_path = os.path.join("uploads", "images", os.path.basename(image_path))
    if os.path.exists(old_path):
        os.remove(old_path)


def require_admin(user: user_dependency) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


admin_dependency = Annotated[dict, Depends(require_admin)]


# ---------- Helpers ----------

def _serialize_comment(comment: Comments, likes: int = 0, dislikes: int = 0) -> dict:
    """Flat dict representation of a comment row."""
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "author_id": comment.author_id,
        "user_name": comment.user_name,
        "parent_id": comment.parent_id,
        "body": comment.body,
        "image_url": comment.image_url,
        "likes_count": likes,
        "dislikes_count": dislikes,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
    }


def _build_comment_tree(flat_comments: list[Comments], counts: dict[int, dict]) -> list[dict]:
    """Nest replies under their top-level parent. One level only.
    ``counts`` maps comment_id → {"likes": int, "dislikes": int}."""
    lookup: dict[int, dict] = {}
    roots: list[dict] = []

    for c in flat_comments:
        c_counts = counts.get(c.id, {"likes": 0, "dislikes": 0})
        lookup[c.id] = {
            **_serialize_comment(c, likes=c_counts["likes"], dislikes=c_counts["dislikes"]),
            "replies": [],
        }

    for c in flat_comments:
        node = lookup[c.id]
        if c.parent_id and c.parent_id in lookup:
            lookup[c.parent_id]["replies"].append(node)
        else:
            roots.append(node)

    return roots


def _get_reaction_counts(db: Session, comment_ids: list[int]) -> dict[int, dict]:
    """Bulk-fetch like/dislike counts for a list of comment ids."""
    if not comment_ids:
        return {}
    rows = (
        db.query(
            CommentReactions.comment_id,
            CommentReactions.reaction,
            func.count(CommentReactions.id).label("cnt"),
        )
        .filter(CommentReactions.comment_id.in_(comment_ids))
        .group_by(CommentReactions.comment_id, CommentReactions.reaction)
        .all()
    )
    counts: dict[int, dict] = {}
    for comment_id, reaction, cnt in rows:
        if comment_id not in counts:
            counts[comment_id] = {"likes": 0, "dislikes": 0}
        counts[comment_id]["likes" if reaction == "like" else "dislikes"] = cnt
    return counts


# ---------- Endpoints ----------

@router.post("/post/{post_id}/new-comment", status_code=status.HTTP_201_CREATED)
async def create_comment(
    post_id: int,  # path param from the URL: /comments/post/{post_id}/new-comment
    body: str = Form(...),
    parent_id: Optional[int] = Form(None),
    image: UploadFile = File(None),
    user: user_dependency = None,
    db: db_dependency = None,
):
    # Validate the post exists and is not deleted.
    post = db.query(Posts).filter(Posts.id == post_id, Posts.deleted_at.is_(None)).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    # If replying, validate the parent comment.
    if parent_id is not None:
        parent = (
            db.query(Comments)
            .filter(Comments.id == parent_id, Comments.deleted_at.is_(None))
            .first()
        )
        if not parent:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parent comment not found")
        if parent.post_id != post_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Parent comment does not belong to this post")
        # Enforce one-level nesting: the parent must itself be a top-level comment.
        if parent.parent_id is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot reply to a reply — only one level of nesting is allowed")

    image_path = save_image(image) if image is not None else None

    # Fetch the user's name from the Users table.
    author = db.query(Users).filter(Users.id == int(user.get("id")), Users.deleted_at.is_(None)).first()
    if not author:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    new_comment = Comments(
        post_id=post_id,
        author_id=int(user.get("id")),
        user_name=author.name,
        parent_id=parent_id,
        body=body,
        image_url=image_path,
    )
    db.add(new_comment)
    db.commit()
    db.refresh(new_comment)
    return {"comment": _serialize_comment(new_comment)}


@router.get("/post/{post_id}", status_code=status.HTTP_200_OK)
async def get_post_comments(post_id: int, db: db_dependency):
    # Public read — same pattern as get_posts.
    post = db.query(Posts).filter(Posts.id == post_id, Posts.deleted_at.is_(None)).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    comments = (
        db.query(Comments)
        .filter(Comments.post_id == post_id, Comments.deleted_at.is_(None))
        .order_by(Comments.created_at.asc())
        .all()
    )
    counts = _get_reaction_counts(db, [c.id for c in comments])
    return {"comments": _build_comment_tree(comments, counts)}


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(comment_id: int, user: admin_dependency, db: db_dependency):
    comment = db.query(Comments).filter(Comments.id == comment_id, Comments.deleted_at.is_(None)).first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    now = datetime.now(timezone.utc)
    comment.deleted_at = now
    delete_image_file(comment.image_url)

    # Cascade: soft-delete direct replies, their images, and all reactions.
    replies = (
        db.query(Comments)
        .filter(Comments.parent_id == comment_id, Comments.deleted_at.is_(None))
        .all()
    )
    for reply in replies:
        reply.deleted_at = now
        delete_image_file(reply.image_url)

    db.query(CommentReactions).filter(
        CommentReactions.comment_id == comment_id,
    ).delete()

    db.commit()
    return {"message": "Comment deleted successfully"}


VALID_REACTIONS = {"like", "dislike"}


@router.post("/{comment_id}/react", status_code=status.HTTP_200_OK)
async def react_to_comment(
    comment_id: int,
    reaction: str = Form(...),
    user: user_dependency = None,
    db: db_dependency = None,
):
    if reaction not in VALID_REACTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reaction must be 'like' or 'dislike'")

    comment = db.query(Comments).filter(Comments.id == comment_id, Comments.deleted_at.is_(None)).first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    user_id = int(user.get("id"))
    existing = (
        db.query(CommentReactions)
        .filter(CommentReactions.comment_id == comment_id, CommentReactions.user_id == user_id)
        .first()
    )

    if existing:
        if existing.reaction == reaction:
            # Same reaction → remove it (toggle off).
            db.delete(existing)
            db.commit()
            return {"message": "Reaction removed"}
        else:
            # Different reaction → flip it.
            existing.reaction = reaction
            db.commit()
            db.refresh(existing)
            return {"message": "Reaction updated", "reaction": existing.reaction}
    else:
        new_reaction = CommentReactions(
            comment_id=comment_id,
            user_id=user_id,
            reaction=reaction,
        )
        db.add(new_reaction)
        db.commit()
        db.refresh(new_reaction)
        return {"message": "Reaction added", "reaction": new_reaction.reaction}
