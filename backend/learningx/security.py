"""Never trust role or course identity from an unauthenticated request."""
import os
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .storage import SessionLocal, User, Member

scheme = HTTPBearer(auto_error=False)
def secret():
    value=os.getenv("NIT_JWT_SECRET","")
    if len(value)<32:
        raise RuntimeError("NIT_JWT_SECRET must be >=32 characters")
    return value

def session():
    with SessionLocal() as db:
        yield db

def issue_token(user_id:str, minutes:int=60):
    now=datetime.now(timezone.utc)
    return jwt.encode({"sub":user_id,"aud":"learningx","iss":"learningx-server",
        "iat":now,"exp":now+timedelta(minutes=minutes)},secret(),algorithm="HS256")

def actor(credentials:HTTPAuthorizationCredentials=Depends(scheme), db:Session=Depends(session)):
    if credentials is None:
        raise HTTPException(401,"Authentication required")
    try:
        p=jwt.decode(credentials.credentials,secret(),algorithms=["HS256"],audience="learningx",issuer="learningx-server",options={"require":["exp","sub","iat"]})
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401,"Invalid or expired token")
    user=db.get(User,p["sub"])
    if user is None or not user.active:
        raise HTTPException(401,"Inactive or unknown user")
    return user

def require_course(db:Session, course_id:str, user:User, required:str):
    # Membership role is bound to DB, never to client-provided token claims.
    m=db.query(Member).filter_by(course_id=course_id,user_id=user.id).first()
    if not m or (m.role!=required and user.role!="admin"):
        raise HTTPException(403,"Course access denied")
    return m
