from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from starlette import status
from app.services.email import send_contact_email

router = APIRouter(
    prefix="/contact",
    tags=["contact"],
)


class ContactRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    message: str


@router.post("", status_code=status.HTTP_200_OK)
async def contact(data: ContactRequest):
    try:
        send_contact_email(
            name=data.name,
            sender_email=data.email,
            phone=data.phone,
            message=data.message,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )
    return {"message": "Email sent successfully"}
