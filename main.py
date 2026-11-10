from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from starlette import status
from typing import Annotated
from app.routes.auth import router as auth_router, get_curent_user
from app.routes.posts import router as posts_router
from app.routes.contact import router as contact_router
from app.routes.comments import router as comments_router
import app.models as models
from app.database import engine, SessionLocal
from sqlalchemy.orm import Session
from fastapi.staticfiles import StaticFiles
import os

app = FastAPI()

origins = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(posts_router)
app.include_router(contact_router)
app.include_router(comments_router)

os.makedirs("uploads/images", exist_ok=True)
app.mount("/uploads/images", StaticFiles(directory="uploads/images"), name="images")

models.Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict, Depends(get_curent_user)]

@app.get("/", status_code=status.HTTP_200_OK)
async def user(user: user_dependency, db: db_dependency):
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication failed")
    return {"User": user}