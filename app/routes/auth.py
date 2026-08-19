from datetime import datetime, timedelta, timezone
import os
import jwt
from jwt import PyJWTError
from dotenv import load_dotenv
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette import status
from app.database import SessionLocal
from app.models import Users
import bcrypt
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from starlette.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Annotated
import uuid
import shutil

load_dotenv()

router = APIRouter(
    prefix="/auth",
    tags=["auth"]
)

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")
ACCESS_TOKEN_EXPIRE_MINUTES = 60

oauth2_bearer = OAuth2PasswordBearer(tokenUrl="/auth/token")

class CreateUserRequest(BaseModel):
    name: str
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def get_curent_user(token: Annotated[str, Depends(oauth2_bearer)], db: Annotated[Session, Depends(get_db)]):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        user_id: str = payload.get("id")
        image_url: str = payload.get("image_url")
        if email is None or user_id is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")
    except PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Could not validate credentials")

    user = db.query(Users).filter(Users.email == email, Users.id == user_id, Users.deleted_at.is_(None)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account has been deleted")

    # role is read from the DB row (not the JWT) so role changes take effect
    # immediately without re-issuing a token.
    return {"email": email, "id": user_id, "image_url": image_url, "role": user.role}

db_dependency = Annotated[Session, Depends(get_db)]
user_dependency = Annotated[dict, Depends(get_curent_user)]

@router.post("/create-user", status_code=status.HTTP_201_CREATED)
async def create_user(db: db_dependency, create_user_request: CreateUserRequest):
    user = db.query(Users).filter(Users.email == create_user_request.email, Users.deleted_at.is_(None)).first()
    if user:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error_code": 400, "detail": "User already exists"}
        )
    create_user_model = Users(
        name=create_user_request.name,
        email=create_user_request.email,
        hashed_password=bcrypt.hashpw(create_user_request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    )
    db.add(create_user_model)
    db.commit()
    db.refresh(create_user_model)

    token = create_access_token(
        create_user_model.email,
        create_user_model.id,
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    return {"user": create_user_model, "jwt": token, "token_type": "bearer"}

@router.delete("/delete-user/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user: user_dependency, db: db_dependency):
    user_id = user.get("id")
    user = db.query(Users).filter(Users.id == user_id, Users.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return {"message": "User deleted successfully"}

@router.post("/login")
async def login_json(login_request: LoginRequest, db: db_dependency):
    user = authenticate_user(login_request.email, login_request.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password"
        )
    token = create_access_token(
        user.email,
        user.id,
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"user": user, "jwt": token, "token_type": "bearer"}


@router.get("/me")
async def get_me(user: user_dependency):
    return {"User": user}

@router.patch("/update-user/{user_id}", status_code=status.HTTP_200_OK)
def update_user(
    user: user_dependency, 
    db: db_dependency, 
    name: str = Form(None),
    email: str = Form(None),
    password: str = Form(None),
    image: UploadFile = File(None),
    remove_image: bool = Form(False),
    ):
    user_id = user.get("id")
    user = db.query(Users).filter(Users.id == user_id, Users.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if name is not None:
        user.name = name

    if email is not None:
        if db.query(Users).filter(Users.email == email, Users.deleted_at.is_(None)).first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already exists")
        user.email = email

    if password is not None:
        user.hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    if remove_image:
        if user.image_url:
            old_filename = user.image_url.lstrip("/uploads/images/")
            old_path = f"uploads/images/{old_filename}"
            if os.path.exists(old_path):
                os.remove(old_path)
        user.image_url = None
    elif image is not None:
        if not image.content_type or not image.content_type.startswith("image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid image type")

        if user.image_url:
            old_filename = user.image_url.lstrip("/uploads/images/")
            old_path = f"uploads/images/{old_filename}"
            if os.path.exists(old_path):
                os.remove(old_path)

        file_extension = image.filename.split(".")[-1] if image.filename else "png"
        unique_filename = f"{uuid.uuid4()}.{file_extension}"
        file_path = f"uploads/images/{unique_filename}"

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

        user.image_url = file_path

    db.commit()
    db.refresh(user)

    token = create_access_token(user.email, user.id, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    return {"user": user, "jwt": token}


@router.post("/token", response_model=Token)
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()], db: db_dependency):
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    token = create_access_token(
        user.email,
        user.id,
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return {"access_token": token, "token_type": "bearer"}


def authenticate_user(email: str, password: str, db):
    user = db.query(Users).filter(Users.email == email, Users.deleted_at.is_(None)).first()
    if not user:
        return False
    if not bcrypt.checkpw(password.encode('utf-8'), user.hashed_password.encode('utf-8')):
        return False
    return user

def create_access_token(email:str, user_id:str, expires_delta: timedelta):
    encode = {"sub": email, "id": user_id}
    expires = datetime.utcnow() + expires_delta
    encode.update({"exp": expires})
    return jwt.encode(encode, SECRET_KEY, algorithm=ALGORITHM)