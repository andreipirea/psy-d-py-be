from datetime import datetime, timezone
import os
import shutil
import uuid
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from starlette import status
from app.database import SessionLocal
from app.models import Posts, Comments, CommentReactions, CommentImages, Users
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


MAX_COMMENT_IMAGES = 5


def validate_image(image: UploadFile) -> None:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image type")


def save_image(image: UploadFile) -> str:
    validate_image(image)
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

def _serialize_comment(
    comment: Comments,
    likes: int = 0,
    dislikes: int = 0,
    images: Optional[list[str]] = None,
) -> dict:
    """Flat dict representation of a comment row.
    ``image_urls`` merges the legacy single ``image_url`` with the comment_images rows."""
    image_urls = ([comment.image_url] if comment.image_url else []) + (images or [])
    return {
        "id": comment.id,
        "post_id": comment.post_id,
        "author_id": comment.author_id,
        "user_name": comment.user_name,
        "parent_id": comment.parent_id,
        "body": comment.body,
        "image_url": comment.image_url,
        "image_urls": image_urls,
        "likes_count": likes,
        "dislikes_count": dislikes,
        "created_at": comment.created_at,
        "updated_at": comment.updated_at,
    }


def _build_comment_tree(
    flat_comments: list[Comments],
    counts: dict[int, dict],
    images: dict[int, list[str]],
) -> list[dict]:
    """Nest replies under their top-level parent. One level only.
    ``counts`` maps comment_id → {"likes": int, "dislikes": int};
    ``images`` maps comment_id → ordered image paths."""
    lookup: dict[int, dict] = {}
    roots: list[dict] = []

    for c in flat_comments:
        c_counts = counts.get(c.id, {"likes": 0, "dislikes": 0})
        lookup[c.id] = {
            **_serialize_comment(
                c,
                likes=c_counts["likes"],
                dislikes=c_counts["dislikes"],
                images=images.get(c.id, []),
            ),
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


def _get_comment_images(db: Session, comment_ids: list[int]) -> dict[int, list[str]]:
    """Bulk-fetch attached image paths for a list of comment ids, in upload order."""
    if not comment_ids:
        return {}
    rows = (
        db.query(CommentImages)
        .filter(CommentImages.comment_id.in_(comment_ids))
        .order_by(CommentImages.comment_id, CommentImages.position, CommentImages.id)
        .all()
    )
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row.comment_id, []).append(row.image_url)
    return result


def _delete_comment_images(db: Session, comment: Comments) -> None:
    """Remove a comment's image files (legacy + attached) and its comment_images rows."""
    delete_image_file(comment.image_url)
    rows = db.query(CommentImages).filter(CommentImages.comment_id == comment.id).all()
    for row in rows:
        delete_image_file(row.image_url)
        db.delete(row)


# ---------- Endpoints ----------

@router.post("/post/{post_id}/new-comment", status_code=status.HTTP_201_CREATED)
async def create_comment(
    post_id: int,  # path param from the URL: /comments/post/{post_id}/new-comment
    body: str = Form(...),
    parent_id: Optional[int] = Form(None),
    images: Optional[list[UploadFile]] = File(None),
    user: user_dependency = None,
    db: db_dependency = None,
):
    images = [img for img in (images or []) if img is not None and img.filename]
    if len(images) > MAX_COMMENT_IMAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"A comment can have at most {MAX_COMMENT_IMAGES} images",
        )
    # Validate every file before writing any, so a bad one never leaves orphans on disk.
    for img in images:
        validate_image(img)

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
        image_url=None,
    )
    db.add(new_comment)
    db.flush()  # assigns new_comment.id for the image rows

    image_paths: list[str] = []
    try:
        for position, img in enumerate(images):
            path = save_image(img)
            image_paths.append(path)
            db.add(CommentImages(comment_id=new_comment.id, image_url=path, position=position))
        db.commit()
    except Exception:
        db.rollback()
        for path in image_paths:
            delete_image_file(path)
        raise

    db.refresh(new_comment)
    return {"comment": _serialize_comment(new_comment, images=image_paths)}


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
    comment_ids = [c.id for c in comments]
    counts = _get_reaction_counts(db, comment_ids)
    images = _get_comment_images(db, comment_ids)
    return {"comments": _build_comment_tree(comments, counts, images)}


@router.delete("/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(comment_id: int, user: admin_dependency, db: db_dependency):
    comment = db.query(Comments).filter(Comments.id == comment_id, Comments.deleted_at.is_(None)).first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")

    now = datetime.now(timezone.utc)
    comment.deleted_at = now
    _delete_comment_images(db, comment)

    # Cascade: soft-delete direct replies, their images, and all reactions.
    replies = (
        db.query(Comments)
        .filter(Comments.parent_id == comment_id, Comments.deleted_at.is_(None))
        .all()
    )
    for reply in replies:
        reply.deleted_at = now
        _delete_comment_images(db, reply)

    # Reactions on the comment and on its replies.
    reacted_ids = [comment_id] + [r.id for r in replies]
    db.query(CommentReactions).filter(
        CommentReactions.comment_id.in_(reacted_ids),
    ).delete(synchronize_session=False)

    db.commit()
    return {"message": "Comment deleted successfully"}


VALID_REACTIONS = {"like", "dislike"}


def _reaction_state(db: Session, comment_id: int, reaction: Optional[str]) -> dict:
    """Fresh counts for one comment plus the caller's current reaction (None = no reaction)."""
    counts = _get_reaction_counts(db, [comment_id]).get(comment_id, {"likes": 0, "dislikes": 0})
    return {
        "comment_id": comment_id,
        "reaction": reaction,
        "likes_count": counts["likes"],
        "dislikes_count": counts["dislikes"],
    }


@router.get("/post/{post_id}/my-reactions", status_code=status.HTTP_200_OK)
async def get_my_reactions(post_id: int, user: user_dependency, db: db_dependency):
    """The logged-in user's reactions on a post's comments, as {comment_id: "like" | "dislike"}.
    Kept separate from the public comment listing so that one stays cacheable and token-free."""
    rows = (
        db.query(CommentReactions.comment_id, CommentReactions.reaction)
        .join(Comments, Comments.id == CommentReactions.comment_id)
        .filter(
            Comments.post_id == post_id,
            Comments.deleted_at.is_(None),
            CommentReactions.user_id == int(user.get("id")),
        )
        .all()
    )
    return {"reactions": {comment_id: reaction for comment_id, reaction in rows}}


@router.post("/{comment_id}/react", status_code=status.HTTP_200_OK)
async def react_to_comment(
    comment_id: int,
    reaction: str = Form(...),
    user: user_dependency = None,
    db: db_dependency = None,
):
    """Toggle a like/dislike. Same reaction again removes it; the other one flips it.
    Returns the new counts and the caller's resulting reaction."""
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

    if existing and existing.reaction == reaction:
        db.delete(existing)
        result: Optional[str] = None
    elif existing:
        existing.reaction = reaction
        result = reaction
    else:
        db.add(CommentReactions(comment_id=comment_id, user_id=user_id, reaction=reaction))
        result = reaction

    try:
        db.commit()
    except IntegrityError:
        # A concurrent request (e.g. a double click) inserted the row first; the unique
        # (comment_id, user_id) index rejected this one. Report what is actually stored.
        db.rollback()
        stored = (
            db.query(CommentReactions.reaction)
            .filter(CommentReactions.comment_id == comment_id, CommentReactions.user_id == user_id)
            .scalar()
        )
        return _reaction_state(db, comment_id, stored)

    return _reaction_state(db, comment_id, result)
