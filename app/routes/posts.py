from datetime import datetime, timezone
import os
import re
import shutil
import unicodedata
import uuid
from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from starlette import status
from app.database import SessionLocal
from app.models import Posts
from app.routes.auth import get_curent_user

router = APIRouter(prefix="/posts", tags=["posts"])

VALID_STATUSES = {"draft", "published"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict, Depends(get_curent_user)]


def slugify(value: str) -> str:
    # SIMPLIFICATION: strips diacritics; extend if accented slugs are needed.
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "post"


def slug_exists(db: Session, slug: str, exclude_id: Optional[int] = None) -> bool:
    query = db.query(Posts).filter(Posts.slug == slug, Posts.deleted_at.is_(None))
    if exclude_id is not None:
        query = query.filter(Posts.id != exclude_id)
    return query.first() is not None


def save_image(image: UploadFile) -> str:
    # Mirrors the user image upload convention in app/routes/auth.py.
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


def get_post_or_404(db: Session, post_id: int) -> Posts:
    post = db.query(Posts).filter(Posts.id == post_id, Posts.deleted_at.is_(None)).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


def require_admin(user: user_dependency) -> dict:
    # role comes from the DB row via get_curent_user (auth.py), not the JWT.
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required")
    return user


admin_dependency = Annotated[dict, Depends(require_admin)]


@router.get("", status_code=status.HTTP_200_OK)
async def get_posts(db: db_dependency):
    # Public read endpoint: the public site calls it without a JWT. The CMS
    # still works because passing a token to a non-auth route is ignored.
    posts = db.query(Posts).filter(Posts.deleted_at.is_(None)).order_by(Posts.created_at.desc()).all()
    return {"posts": posts}


@router.get("/show-deleted-posts", status_code=status.HTTP_200_OK)
async def get_deleted_posts(user: admin_dependency, db: db_dependency):
    posts = (
        db.query(Posts)
        .filter(Posts.deleted_at.is_not(None))
        .order_by(Posts.deleted_at.desc())
        .all()
    )
    return {"posts": posts}


@router.post("/restore-deleted-post/{post_id}", status_code=status.HTTP_200_OK)
async def restore_post(post_id: int, user: admin_dependency, db: db_dependency):
    post = db.query(Posts).filter(Posts.id == post_id, Posts.deleted_at.is_not(None)).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found or not deleted")
    post.deleted_at = None
    db.commit()
    db.refresh(post)
    return {"post": post}


@router.get("/{post_id}", status_code=status.HTTP_200_OK)
async def get_post(post_id: int, user: user_dependency, db: db_dependency):
    post = db.query(Posts).filter(Posts.id == post_id, Posts.deleted_at.is_(None)).first()
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    return {"post": post}


@router.post("/create-post", status_code=status.HTTP_201_CREATED)
async def create_post(
    user: admin_dependency,
    db: db_dependency,
    title: str = Form(...),
    slug: str = Form(None),
    excerpt: str = Form(None),
    content: str = Form(None),
    status_val: str = Form("draft", alias="status"),
    featured_image: UploadFile = File(None),
):
    if status_val not in VALID_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")

    active_slug = slug.strip() if slug and slug.strip() else slugify(title)
    if slug_exists(db, active_slug):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")

    image_path = save_image(featured_image) if featured_image is not None else None

    new_post = Posts(
        title=title,
        slug=active_slug,
        excerpt=excerpt,
        content=content,
        featured_image=image_path,
        status=status_val,
        published_at=datetime.now(timezone.utc) if status_val == "published" else None,
        author_id=int(user.get("id")),
    )
    db.add(new_post)
    db.commit()
    db.refresh(new_post)
    return {"post": new_post}


@router.patch("/update-post/{post_id}", status_code=status.HTTP_200_OK)
async def update_post(
    post_id: int,
    user: admin_dependency,
    db: db_dependency,
    title: str = Form(None),
    slug: str = Form(None),
    excerpt: str = Form(None),
    content: str = Form(None),
    status_val: str = Form(None, alias="status"),
    featured_image: UploadFile = File(None),
    remove_image: bool = Form(False),
):
    post = get_post_or_404(db, post_id)

    if title is not None:
        post.title = title

    if slug is not None and slug.strip():
        if slug_exists(db, slug.strip(), exclude_id=post.id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Slug already exists")
        post.slug = slug.strip()

    if excerpt is not None:
        post.excerpt = excerpt
    if content is not None:
        post.content = content

    if status_val is not None:
        if status_val not in VALID_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid status")
        if status_val == "published" and post.published_at is None:
            post.published_at = datetime.now(timezone.utc)
        post.status = status_val

    if remove_image:
        delete_image_file(post.featured_image)
        post.featured_image = None
    elif featured_image is not None:
        delete_image_file(post.featured_image)
        post.featured_image = save_image(featured_image)

    db.commit()
    db.refresh(post)
    return {"post": post}


@router.delete("/delete-post/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_post(post_id: int, user: admin_dependency, db: db_dependency):
    post = get_post_or_404(db, post_id)
    post.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "Post deleted successfully"}
