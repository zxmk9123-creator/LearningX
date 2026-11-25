"""Never trust role or course identity from an unauthenticated request."""
import os
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .storage import CourseDeletion, SessionLocal, User, Member

scheme = HTTPBearer(auto_error=False)
def secret():
    value=os.getenv("NIT_JWT_SECRET","")
    if len(value)<32:
        raise RuntimeError("NIT_JWT_SECRET must be >=32 characters")
    return value

def session():
    with SessionLocal() as db:
        yield db

# A signed-in tab renews its token while it stays open, so a class that runs past the
# token lifetime is not cut off. Renewal never extends a sign-in beyond this age.
MAX_SIGN_IN_HOURS = 14

def issue_token(user_id:str, minutes:int=60, *, session_id:str|None=None, auth_time:int|None=None):
    now=datetime.now(timezone.utc)
    claims={"sub":user_id,"aud":"learningx","iss":"learningx-server",
        "iat":now,"exp":now+timedelta(minutes=minutes),
        "auth_time":auth_time if auth_time is not None else int(now.timestamp())}
    if session_id:
        claims.update(scope="classroom",session_id=session_id)
    return jwt.encode(claims,secret(),algorithm="HS256")

def renew_token(claims:dict):
    """A fresh token with the same identity, scope and lifetime as a still-valid one."""
    now=int(datetime.now(timezone.utc).timestamp())
    auth_time=int(claims.get("auth_time") or claims["iat"])
    if now-auth_time>MAX_SIGN_IN_HOURS*3600:
        raise HTTPException(401,"Sign-in is too old to renew; sign in again")
    minutes=max(1,min(240,round((int(claims["exp"])-int(claims["iat"]))/60)))
    token=issue_token(claims["sub"],minutes,session_id=claims.get("session_id"),auth_time=auth_time)
    return {"access_token":token,"expires_in":minutes*60}

def decode_token(credentials):
    if credentials is None:
        raise HTTPException(401,"Authentication required")
    try:
        return jwt.decode(credentials.credentials,secret(),algorithms=["HS256"],audience="learningx",issuer="learningx-server",options={"require":["exp","sub","iat"]})
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401,"Invalid or expired token")

def actor(request:Request, credentials:HTTPAuthorizationCredentials=Depends(scheme), db:Session=Depends(session)):
    p=decode_token(credentials)
    user=db.get(User,p["sub"])
    if user is None or not user.active:
        raise HTTPException(401,"Inactive or unknown user")
    _restrict_classroom_actor(request,p,db,user)
    return user

# Request-scoped facts already checked against the database in this request (db.info).
VERIFIED_COURSE="learningx_verified_course"
PARTICIPANT="learningx_participant"
ANCHORS="learningx_has_anchor"  # question id -> has a quoted anchor (anchors are written only when asked)
HEADS="learningx_heads"  # question id -> current QuestionHead (or None) read with the access check
CHAT_HINT="learningx_chat_hint"  # chat switch as read when the request started; writes re-read it under the lock
KEEP="learningx_keep"


def keep(db, *objects):
    """Hold objects for the rest of the request: the identity map only keeps weak references,
    so an object nobody holds is read again by the next db.get for the same row."""
    db.info.setdefault(KEEP, []).extend(o for o in objects if o is not None)


def require_course(db:Session, course_id:str, user:User, required:str):
    # Membership role is bound to DB, never to client-provided token claims.
    verified=db.info.get(VERIFIED_COURSE)
    if verified and verified[:3]==(course_id,user.id,required):
        return verified[3]
    # One round trip for the membership and the deletion marker.
    found=db.query(Member,CourseDeletion.course_id).select_from(Member).outerjoin(
        CourseDeletion,CourseDeletion.course_id==Member.course_id).filter(
        Member.course_id==course_id,Member.user_id==user.id).first()
    m,deleted=found if found else (None,None)
    if not m or (m.role!=required and user.role!="admin"):
        raise HTTPException(403,"Course access denied")
    if deleted:
        raise HTTPException(404,"Course not found")
    db.info[VERIFIED_COURSE]=(course_id,user.id,required,m)
    return m


def _restrict_classroom_actor(request, claims, db, user):
    """Anonymous join grants access to one live session, never course APIs."""
    scope = claims.get("scope")
    anonymous = user.email.endswith("@join.question-up.local")
    if scope is None and not anonymous:
        return
    if scope != "classroom" or not claims.get("session_id"):
        raise HTTPException(403,"Session-scoped authentication required")
    route = getattr(request.scope.get("route"), "path", "")
    allowed = {
        ("GET", "/v1/sessions/{session_id}"),
        ("GET", "/v1/sessions/{session_id}/mine"),
        ("GET", "/v1/sessions/{session_id}/live"),
        ("PUT", "/v1/sessions/{session_id}/live/confusion"),
        ("GET", "/v1/sessions/{session_id}/live/page/{version}"),
        ("GET", "/v1/sessions/{session_id}/browse/{page}"),
        ("POST", "/v1/auth/refresh"),
        ("POST", "/v1/sessions/{session_id}/questions"),
        ("POST", "/v1/session-questions/{row_id}/revisions"),
        ("POST", "/v1/session-questions/{row_id}/submit"),
        ("POST", "/v1/session-questions/{row_id}/suitability/check"),
        ("POST", "/v1/sessions/{session_id}/questions/submit-batch"),
    }
    if (request.method,route) not in allowed:
        raise HTTPException(403,"Session token cannot access this endpoint")
    # Imported lazily after router initialization to avoid a module import cycle.
    from .classroom import ClassSession, SessionParticipant, SessionQuestion
    bound = claims["session_id"]
    target = request.path_params.get("session_id")
    if route == "/v1/auth/refresh":
        target = bound  # renewal keeps the token's own class; membership is still checked below
    if "row_id" in request.path_params:
        from .classroom import QuestionAnchor
        from .suitability import QuestionHead, has_quote
        # One round trip: the question, its quoted anchor and its current draft version.
        found_row = (db.query(SessionQuestion, QuestionAnchor, QuestionHead).select_from(SessionQuestion)
                     .outerjoin(QuestionAnchor, QuestionAnchor.question_id == SessionQuestion.id)
                     .outerjoin(QuestionHead, QuestionHead.session_question_id == SessionQuestion.id)
                     .filter(SessionQuestion.id == request.path_params["row_id"]).first())
        row, anchor, head = found_row if found_row else (None, None, None)
        if not row or row.student_id != user.id:
            raise HTTPException(404,"Question not found")
        keep(db, row, anchor, head)
        db.info.setdefault(ANCHORS, {})[row.id] = has_quote(anchor)
        db.info.setdefault(HEADS, {})[row.id] = head
        target = row.session_id
    if target != bound:
        raise HTTPException(403,"Token belongs to a different session")
    from sqlalchemy.orm import aliased
    from .classroom import SessionChatAccess
    from .live_slides import LiveSlide
    from .storage import Course
    from .suitability import SessionPolicy
    owner = aliased(User)
    # One round trip: participation, the class, its chat switch, the course membership and
    # deletion marker the route checks next (a participant row implies the class exists), and
    # rows the question routes read later (policy, live page, course and its owner). Read
    # without locks: every write re-reads what it depends on under the session lock.
    found = (db.query(ClassSession, SessionChatAccess.open, Member, CourseDeletion.course_id,
                      SessionPolicy, LiveSlide, Course, owner)
             .select_from(ClassSession)
             .join(SessionParticipant, SessionParticipant.session_id == ClassSession.id)
             .outerjoin(SessionChatAccess, SessionChatAccess.session_id == ClassSession.id)
             .outerjoin(Member, (Member.course_id == ClassSession.course_id) & (Member.user_id == user.id))
             .outerjoin(CourseDeletion, CourseDeletion.course_id == ClassSession.course_id)
             .outerjoin(SessionPolicy, SessionPolicy.session_id == ClassSession.id)
             .outerjoin(LiveSlide, LiveSlide.session_id == ClassSession.id)
             .outerjoin(Course, Course.id == ClassSession.course_id)
             .outerjoin(owner, owner.id == Course.created_by)
             .filter(ClassSession.id == bound, SessionParticipant.user_id == user.id).first())
    if not found:
        raise HTTPException(403,"Not a participant of this session")
    current, chat, member, deleted, *prefetched = found
    keep(db, current, member, *prefetched)
    db.info[PARTICIPANT] = (bound, user.id)
    db.info[CHAT_HINT] = (bound, bool(chat))
    if member is not None and not deleted:
        db.info[VERIFIED_COURSE] = (current.course_id, user.id, member.role, member)
    if request.method == "POST" and current.status != "OPEN" and route != "/v1/auth/refresh":
        raise HTTPException(423,"The class is not taking questions right now")
