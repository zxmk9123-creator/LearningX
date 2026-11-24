"""Google sign-in through Neon Auth (Managed Better Auth) for faculty and students.

The static pages sign in with Neon Auth directly (Google, Neon's shared OAuth
credentials for now) and send the Neon-issued JWT here. This module verifies it
against Neon's JWKS (EdDSA, iss = aud = the auth origin) and exchanges it for a
LearningX token, so the existing APIs and permission checks stay unchanged. No
cookie is shared between the two origins.

Who gets in: a Google-verified email on an allowed school domain
(NIT_GOOGLE_ALLOWED_DOMAINS). The first sign-in chooses student or faculty once;
the choice is stored and later sign-ins are classified from the database. An
existing account with the same verified email keeps its stored role. Accounts
are linked by the Neon user id (``sub``) after that, never by email again.

Master accounts (operators' test accounts) choose a role on every sign-in: the
choice and the Google link are not stored, and each role works on its own
account so classes and questions made while testing are kept per role.
"""
import base64
import hashlib
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .security import issue_token, secret, session
from .storage import Base, User

# Public endpoint of the learningx-pilot main branch (not a secret); override per environment.
DEFAULT_AUTH_URL = "https://ep-late-darkness-biqqwodu.neonauth.c-5.ap-southeast-1.aws.neon.tech/neondb/auth"
JWKS_TTL_SECONDS = 600
DEFAULT_ALLOWED_DOMAINS = "hanyang.ac.kr"
STUDENT_TICKET_MINUTES = 30
# SHA-256 of the master accounts' lowercase emails (kept out of the repository in plain text).
# NIT_MASTER_EMAILS (comma-separated plain emails) adds more without a code change.
MASTER_EMAIL_SHA256 = frozenset({
    "a2cf003557a3b4ea1886f06589985e4dc3a0baddeff2bb17115c526e74c92ee0",
    "4f438efc48fdc181c4500dfbf8621571e4202cc6baf02ffa4801fef9fd103ec4",
    "b36ae8e0cf2169cf3e15bfa9f66811b8f3e064eb918f54345de06e2f9f1f6839",
    "3ac1cf5b4d68ef05a809c7a1816b0ebd2e1e190640c9c223b7e0021ec2347376",
})

router = APIRouter(prefix="/v1")


def auth_url() -> str:
    return os.getenv("NIT_NEON_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/")


def auth_origin() -> str:
    parsed = urlparse(auth_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def allowed_domains() -> list[str]:
    raw = os.getenv("NIT_GOOGLE_ALLOWED_DOMAINS", DEFAULT_ALLOWED_DOMAINS)
    return [d.strip().lower().lstrip("@.") for d in raw.split(",") if d.strip()]


def domain_allowed(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in allowed_domains())


def is_master(email: str) -> bool:
    extra = {e.strip().lower() for e in os.getenv("NIT_MASTER_EMAILS", "").split(",") if e.strip()}
    return email in extra or hashlib.sha256(email.encode()).hexdigest() in MASTER_EMAIL_SHA256


def _master_user(db, email: str, role: str) -> User:
    """The working account of a master for one role (created on first use, then reused)."""
    plain = db.query(User).filter(User.email == email).first()
    if plain is not None and plain.role == role and plain.active:
        return plain  # keeps anything made before the account became a master
    local, domain = email.split("@", 1)
    alias = f"{local}+{role}@{domain}"
    user = db.query(User).filter(User.email == alias).first()
    if user is None:
        user = User(email=alias, role=role, active=True)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            user = db.query(User).filter(User.email == alias).one()
    if not user.active or user.role != role:
        raise HTTPException(403, {"code": "account_inactive"})
    return user


class UserProfile(Base):
    """Display name from the school Google account (new table only). Shown to the class's
    faculty on submitted questions, masked until clicked; never on the projector or CSV."""
    __tablename__ = "user_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 default=lambda: datetime.now(timezone.utc))


def remember_name(db, user: User, claims: dict) -> None:
    """Keep the Google display name current (it may change in the Google account)."""
    name = " ".join(str(claims.get("name") or "").split())[:80]
    if not name:
        return
    profile = db.get(UserProfile, user.id)
    if profile is None:
        db.add(UserProfile(user_id=user.id, display_name=name))
    elif profile.display_name != name:
        profile.display_name = name
        profile.updated_at = datetime.now(timezone.utc)
    else:
        return
    try:
        db.commit()
    except IntegrityError:
        db.rollback()


class GoogleIdentity(Base):
    """A Neon Auth (Google) user linked to one LearningX account (new table only)."""
    __tablename__ = "google_identities"
    provider_sub: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="neon_google")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=lambda: datetime.now(timezone.utc))


# ---------- JWKS and token verification ----------

_jwks = {"keys": {}, "fetched": 0.0, "url": None}
_jwks_lock = threading.Lock()


def _fetch_jwks(url):
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    keys = {}
    for jwk in response.json().get("keys", []):
        if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519" and jwk.get("kid") and jwk.get("x"):
            raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
            keys[jwk["kid"]] = Ed25519PublicKey.from_public_bytes(raw)
    return keys


def signing_key(kid, fetch=None):
    url = auth_url() + "/.well-known/jwks.json"
    with _jwks_lock:
        fresh = _jwks["url"] == url and time.monotonic() - _jwks["fetched"] < JWKS_TTL_SECONDS
        if not fresh or kid not in _jwks["keys"]:
            # Unknown kid: Neon may have rotated keys, so refetch once (rate limited by the TTL check above).
            if fresh and time.monotonic() - _jwks["fetched"] < 10:
                raise HTTPException(401, "Invalid sign-in token")
            try:
                _jwks.update(keys=(fetch or _fetch_jwks)(url), fetched=time.monotonic(), url=url)
            except Exception as exc:
                raise HTTPException(503, "Sign-in verification is unavailable") from exc
        key = _jwks["keys"].get(kid)
    if key is None:
        raise HTTPException(401, "Invalid sign-in token")
    return key


def verify(token: str, fetch=None) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Invalid sign-in token") from exc
    if header.get("alg") != "EdDSA" or not header.get("kid"):
        raise HTTPException(401, "Invalid sign-in token")
    key = signing_key(header["kid"], fetch)
    try:
        claims = jwt.decode(token, key=key, algorithms=["EdDSA"], issuer=auth_origin(), audience=auth_origin(),
                            options={"require": ["exp", "iat", "sub"]}, leeway=30)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Invalid sign-in token") from exc
    if claims.get("banned") or not isinstance(claims.get("sub"), str) or not 0 < len(claims["sub"]) <= 64:
        raise HTTPException(401, "Invalid sign-in token")
    return claims


# ---------- exchange ----------

class NeonSessionIn(BaseModel):
    token: str = Field(min_length=20, max_length=8192)
    # Only read on the very first sign-in; afterwards the stored role decides.
    role: Literal["student", "faculty"] | None = None


def student_ticket(user_id: str) -> str:
    """A short account ticket a Google student presents to /v1/join. It opens no other API:
    the classroom guard rejects any scope but a session-bound one."""
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "aud": "learningx", "iss": "learningx-server", "iat": now,
                       "exp": now + timedelta(minutes=STUDENT_TICKET_MINUTES), "scope": "student_account"},
                      secret(), algorithm="HS256")


def _granted(user: User, master: bool = False) -> dict:
    extra = {"master": True} if master else {}
    if user.role == "faculty":
        return {"role": "faculty", "access_token": issue_token(user.id, minutes=60), "expires_in": 3600, **extra}
    return {"role": "student", "join_ticket": student_ticket(user.id), "expires_in": STUDENT_TICKET_MINUTES * 60, **extra}


@router.post("/auth/neon/session")
def neon_session(data: NeonSessionIn, response: Response, db: Session = Depends(session)):
    claims = verify(data.token)
    response.headers["Cache-Control"] = "no-store"
    email = str(claims.get("email") or "").strip().lower()
    if claims.get("emailVerified") is not True or "@" not in email:
        raise HTTPException(403, {"code": "email_unverified"})
    if not domain_allowed(email):
        raise HTTPException(403, {"code": "domain_not_allowed", "domains": allowed_domains()})
    if is_master(email):
        # Master: a role on every sign-in; neither the choice nor the Google link is stored.
        if data.role is None:
            raise HTTPException(403, {"code": "role_required", "email": email, "master": True})
        user = _master_user(db, email, data.role)
        remember_name(db, user, claims)
        return _granted(user, master=True)
    identity = db.get(GoogleIdentity, claims["sub"])
    if identity is None:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            if data.role is None:
                # First sign-in: the person chooses student or faculty once.
                raise HTTPException(403, {"code": "role_required", "email": email})
            user = User(email=email, role=data.role, active=True)
            db.add(user)
            db.flush()
        elif user.role not in ("student", "faculty"):
            raise HTTPException(403, {"code": "account_inactive"})
        db.add(GoogleIdentity(provider_sub=claims["sub"], user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            # The same account was linked by a parallel sign-in (or to another Google user).
            db.rollback()
            identity = db.get(GoogleIdentity, claims["sub"])
            if identity is None:
                raise HTTPException(409, {"code": "account_already_linked"})
        else:
            identity = db.get(GoogleIdentity, claims["sub"])
    user = db.get(User, identity.user_id)
    if not user or not user.active or user.role not in ("student", "faculty"):
        raise HTTPException(403, {"code": "account_inactive"})
    remember_name(db, user, claims)
    return _granted(user)
