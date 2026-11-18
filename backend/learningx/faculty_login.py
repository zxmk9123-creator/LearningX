"""Exchange Google identity for an existing faculty account's API session."""
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .security import issue_token, session
from .storage import User

router = APIRouter(prefix="/v1/faculty/auth")


def configuration():
    client_id = os.getenv("NIT_GOOGLE_CLIENT_ID", "").strip()
    try:
        accounts = json.loads(os.getenv("NIT_GOOGLE_FACULTY_ACCOUNTS", "{}"))
    except ValueError:
        accounts = None
    if not client_id.endswith(".apps.googleusercontent.com") or not isinstance(accounts, dict) or not accounts:
        raise HTTPException(503, "Faculty sign-in is not configured")
    return client_id, accounts


@router.get("/config")
def config(response: Response):
    client_id, _ = configuration()
    response.headers["Cache-Control"] = "no-store"
    return {"client_id": client_id}


class GoogleCredential(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)


def verify_google(credential, client_id):
    from google.auth import exceptions
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.id_token import verify_oauth2_token

    transport = GoogleRequest()
    def bounded_request(*args, **kwargs):
        kwargs["timeout"] = 5
        return transport(*args, **kwargs)
    try:
        return verify_oauth2_token(credential, bounded_request, client_id)
    except exceptions.TransportError as exc:
        raise HTTPException(503, "Google sign-in temporarily unavailable") from exc
    except (ValueError, exceptions.GoogleAuthError) as exc:
        raise HTTPException(401, "Invalid Google credential") from exc


@router.post("/google")
def sign_in(body: GoogleCredential, request: Request, response: Response,
            db: Session = Depends(session)):
    origins = {item.strip() for item in os.getenv("NIT_CORS_ORIGINS", "").split(",") if item.strip()}
    if request.headers.get("origin") not in origins:
        raise HTTPException(403, "Unapproved sign-in origin")
    client_id, accounts = configuration()
    claims = verify_google(body.credential, client_id)
    subject = claims.get("sub")
    user_id = accounts.get(subject) if isinstance(subject, str) and subject else None
    user = db.get(User, user_id) if isinstance(user_id, str) and user_id else None
    if not user or not user.active or user.role != "faculty":
        raise HTTPException(403, "Google account is not registered as faculty")
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_token(user.id)}
