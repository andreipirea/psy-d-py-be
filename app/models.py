from sqlalchemy import Column, Integer, String, Text, DateTime, func, Index, text, ForeignKey
from app.database import Base


class Users(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Partial unique index: active emails are unique, but a soft-deleted
        # user's email becomes re-registerable (deleted_at IS NULL).
        Index("ix_users_email", "email", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=False, index=True)
    email = Column(String, unique=False, index=False)
    hashed_password = Column(String, unique=False, index=True)
    image_url = Column(String, unique=False, index=True)
    role = Column(String, unique=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)


class Posts(Base):
    __tablename__ = "posts"
    __table_args__ = (
        # Partial unique index: active slugs are unique, but a soft-deleted
        # post's slug becomes reusable (mirrors ix_users_email on users).
        Index("ix_posts_slug", "slug", unique=True, postgresql_where=text("deleted_at IS NULL")),
    )

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    slug = Column(String(255), nullable=False)
    excerpt = Column(Text, nullable=True)
    content = Column(Text, nullable=True)
    featured_image = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, server_default="draft")
    published_at = Column(DateTime(timezone=True), nullable=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)


class Comments(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user_name = Column(String, nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"), nullable=True, index=True)
    body = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime(timezone=True), nullable=True, default=None)


class CommentImages(Base):
    """Photos attached to a comment (up to MAX_COMMENT_IMAGES each).
    Comments.image_url is kept for comments created before multi-image support."""
    __tablename__ = "comment_images"

    id = Column(Integer, primary_key=True, index=True)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    image_url = Column(Text, nullable=False)
    position = Column(Integer, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CommentReactions(Base):
    __tablename__ = "comment_reactions"
    __table_args__ = (
        Index("uq_comment_reaction", "comment_id", "user_id", unique=True),
    )

    id = Column(Integer, primary_key=True, index=True)
    comment_id = Column(Integer, ForeignKey("comments.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reaction = Column(String(10), nullable=False)  # "like" or "dislike"
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
