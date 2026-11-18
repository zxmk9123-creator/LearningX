"""LearningX MVP HTTP API. No unauthenticated data or evidence endpoints."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from contextlib import ExitStack
from typing import Literal
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text
from .storage import init_db, User, Course, Member, Material, Audit, Question, Revision
from .security import actor, session, require_course, secret, issue_token
from . import engine_adapter as nit
from . import private_store
from .ingest_queue import router as ingest_router

app=FastAPI(title="LearningX NIT Backend",version="0.1.0",docs_url=None,redoc_url=None)
# Frontend origins must be explicitly configured; no wildcard CORS with bearer tokens.
origins=[x.strip() for x in os.getenv("NIT_CORS_ORIGINS","").split(",") if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,
        allow_methods=["GET","POST","PUT"],allow_headers=["Authorization","Content-Type"])

@app.on_event("startup")
def bootstrap():
    secret()
    # Render's filesystem is ephemeral by default; do not quietly start with an
    # SQLite database when the intended Neon DATABASE_URL was not provisioned.
    if (os.getenv("RENDER_SERVICE_ID") or os.getenv("NIT_SERVERLESS_RUNTIME") == "1") and not os.getenv("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL must be configured on the backend host")
    init_db()

class CourseCreate(BaseModel):
    title:str=Field(min_length=2,max_length=180)
    policy:Literal["PROHIBITED","RESTRICTED_HINT_ONLY","PERMITTED"]="RESTRICTED_HINT_ONLY"
    teacher_approved:bool=False

class CoursePolicy(BaseModel):
    policy:Literal["PROHIBITED","RESTRICTED_HINT_ONLY","PERMITTED"]
    teacher_approved:bool

class AddMember(BaseModel):
    user_id:str=Field(min_length=1,max_length=64)

class EvidenceItem(BaseModel):
    page:int=Field(ge=1)
    block_id:str=Field(min_length=1,max_length=120)
    block_sha256:str=Field(pattern=r"^[0-9a-f]{64}$")

class Review(BaseModel):
    items:list[EvidenceItem]=Field(min_length=1,max_length=1000)
    acknowledge_unprocessed_media:bool=False

class Ask(BaseModel):
    material_id:str=Field(min_length=1,max_length=64)
    question:str=Field(min_length=10,max_length=2000)

class Revise(BaseModel):
    text:str=Field(min_length=10,max_length=2000)

def audit(db,who,course,action,material=None,detail=""):
    db.add(Audit(actor_id=who,course_id=course,action=action,material_id=material,detail=detail[:400]))

def material_for(db,material_id,course_id=None):
    m=db.get(Material,material_id)
    if not m or (course_id and m.course_id!=course_id):raise HTTPException(404,"Material not found")
    return m

@app.get("/health")
def health():
    return {"status":"ok","service":"learningx-backend"}

@app.get("/health/db")
def database_health(db:Session=Depends(session)):
    """Read-only check; suppress connection strings and SQL exception details."""
    try:
        db.execute(sql_text("SELECT 1"))
        return {"status":"ok","database":"connected"}
    except Exception as exc:
        raise HTTPException(503, "Database unavailable") from exc

class FacultyBetaInvite(BaseModel):
    code: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")

@app.post("/v1/beta/faculty/session")
def faculty_beta_session(data: FacultyBetaInvite, response: Response, db: Session = Depends(session)):
    """Exchange an operator-issued, per-faculty 256-bit invite for a short session."""
    try:
        invites = json.loads(os.getenv("NIT_BETA_FACULTY_INVITES", "{}"))
    except ValueError:
        invites = {}
    if not isinstance(invites, dict) or not invites:
        raise HTTPException(503, "Faculty beta is not enabled")
    digest = hashlib.sha256(data.code.encode("ascii")).hexdigest()
    user_id = invites.get(digest)
    user = db.get(User, user_id) if isinstance(user_id, str) and user_id else None
    if not user or not user.active or user.role != "faculty":
        raise HTTPException(401, "Invalid beta invitation")
    if os.getenv("NIT_WORKER_BACKEND") != "northflank_poll":
        raise HTTPException(503, "Beta analysis worker is not configured")
    private_store.require_store()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_token(user.id, minutes=60)}

@app.post("/v1/courses",status_code=201)
def create_course(data:CourseCreate,db:Session=Depends(session),user:User=Depends(actor)):
    if user.role not in ("faculty","admin"):raise HTTPException(403,"Faculty only")
    c=Course(title=data.title,policy=data.policy,teacher_approved=data.teacher_approved,created_by=user.id)
    db.add(c);db.flush()
    db.add(Member(course_id=c.id,user_id=user.id,role="faculty"))
    audit(db,user.id,c.id,"course_created")
    db.commit()
    return {"id":c.id,"title":c.title,"policy":c.policy}

@app.post("/v1/courses/{course_id}/members",status_code=201)
def add_student(course_id:str,data:AddMember,db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    target=db.get(User,data.user_id)
    if not target or not target.active or target.role!="student":raise HTTPException(404,"Student account not found")
    if db.query(Member).filter_by(course_id=course_id,user_id=target.id).first():
        raise HTTPException(409,"Already enrolled")
    db.add(Member(course_id=course_id,user_id=target.id,role="student"))
    audit(db,user.id,course_id,"student_enrolled")
    db.commit()
    return {"enrolled":True,"course_id":course_id,"student_id":target.id}

@app.put("/v1/courses/{course_id}/policy")
def update_policy(course_id:str,data:CoursePolicy,db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    c=db.get(Course,course_id)
    if not c:raise HTTPException(404,"Course not found")
    c.policy=data.policy;c.teacher_approved=data.teacher_approved
    audit(db,user.id,course_id,"course_policy_updated",detail=data.policy)
    db.commit()
    return {"policy":c.policy,"teacher_approved":c.teacher_approved,
        "notice":"This course policy is NOT a faculty evidence signature"}

@app.post("/v1/courses/{course_id}/materials",status_code=201)
async def upload_material(course_id:str,file:UploadFile=File(...),db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    if not db.get(Course,course_id):raise HTTPException(404,"Course not found")
    # A Vercel function cannot persist teacher evidence in /tmp.
    durable=private_store.durable_upload_enabled()
    if private_store.serverless():
        # On Vercel, even a 32.9MB request never reaches FastAPI: 4.5MB limit.
        raise HTTPException(410,"Use /v1/courses/{id}/uploads/initiate for direct upload")
    filename=Path(file.filename or "").name
    ext=Path(filename).suffix.lower()
    if ext not in (".pdf",".pptx"):raise HTTPException(415,"PDF or PPTX only")
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise HTTPException(413,"File too large")
    if not ((ext==".pdf" and data.startswith(b"%PDF-")) or
            (ext==".pptx" and data.startswith(b"PK"))):
        raise HTTPException(422,"Invalid file signature")
    if os.getenv("RENDER_SERVICE_ID") and not durable:
        raise HTTPException(503,"Durable document storage is required before uploads")
    mid=os.urandom(16).hex()
    with ExitStack() as scope:
        if durable:
            directory=Path(scope.enter_context(tempfile.TemporaryDirectory(prefix="learningx-ingest-")))
        else:
            directory=Path(os.getenv("NIT_STORAGE_DIR","./private_lessons")).resolve()/mid
            directory.mkdir(parents=True,exist_ok=False)
        try:
            original=directory/("source"+ext)
            original.write_bytes(data)
            sha=hashlib.sha256(data).hexdigest()
            out=directory/"parsed"
            document=nit.ingest(original,out)
            if document["source"]["sha256"]!=sha:
                raise HTTPException(422,"Parser source digest mismatch")
            location=(private_store.store_after_ingest(directory,course_id,mid)
                      if durable else str(out))
            record=Material(id=mid,course_id=course_id,filename=filename[:260],sha256=sha,
                out_dir=location,ingested_by=user.id)
            db.add(record)
            audit(db,user.id,course_id,"material_ingested",mid)
            db.commit()
            return {"id":mid,"filename":filename,"pages":len(document["pages"]),
                "status":"PENDING_FACULTY_REVIEW","approved_count":0}
        except Exception:
            db.rollback()
            if not durable:shutil.rmtree(directory,ignore_errors=True)
            raise

@app.get("/v1/materials/{material_id}/candidates")
def list_candidates(material_id:str,db:Session=Depends(session),user:User=Depends(actor)):
    m=material_for(db,material_id)
    require_course(db,m.course_id,user,"faculty")
    with private_store.material_workspace(m) as out:
        return {"material_id":m.id,"eligible_candidates":nit.candidates(out),
            "approved_count":nit.verified_count(out)}

@app.post("/v1/materials/{material_id}/approve")
def approve(material_id:str,data:Review,db:Session=Depends(session),user:User=Depends(actor)):
    m=db.query(Material).filter_by(id=material_id).with_for_update().first()
    if not m:raise HTTPException(404,"Material not found")
    require_course(db,m.course_id,user,"faculty")
    with private_store.material_workspace(m) as out:
        # The instructor must explicitly acknowledge omitted narration.
        doc=json.loads((out/"document.json").read_text(encoding="utf-8"))
        if doc.get("source",{}).get("type")=="pptx":
            coverage=doc.get("media_coverage")
            if not isinstance(coverage,dict) or "audio_status" not in coverage:
                raise HTTPException(503,"PPTX media coverage is not verified")
            if coverage["audio_status"]=="NOT_ANALYZED" and not data.acknowledge_unprocessed_media:
                raise HTTPException(409,"PPTX narration is not analyzed; acknowledge before approval")
        result=nit.approve_material(out,user.id,[x.model_dump() for x in data.items])
        private_store.persist_approval(m,out)
    audit(db,user.id,m.course_id,"evidence_signed",m.id,detail=f"count={result['approved']}")
    db.commit()
    return {"material_id":m.id,"approved_count":result["approved"],"status":"FACULTY_SIGNED"}

@app.post("/v1/courses/{course_id}/questions",status_code=201)
def ask(course_id:str,data:Ask,db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"student")
    m=material_for(db,data.material_id,course_id)
    c=db.get(Course,course_id)
    if not c:raise HTTPException(404,"Course not found")
    with private_store.material_workspace(m) as out:
        result=nit.coach_question(out,user.id,data.question,c.policy,c.teacher_approved)
    q=Question(course_id=course_id,material_id=m.id,student_id=user.id,
        text=data.question,feedback_json=json.dumps(result,ensure_ascii=False))
    db.add(q);db.flush()
    audit(db,user.id,course_id,"question_coached",m.id,detail=result["mode"])
    db.commit()
    return {"question_id":q.id,"coaching":result}

@app.post("/v1/questions/{question_id}/revisions",status_code=201)
def revise(question_id:str,data:Revise,db:Session=Depends(session),user:User=Depends(actor)):
    q=db.get(Question,question_id)
    if not q:raise HTTPException(404,"Question not found")
    require_course(db,q.course_id,user,"student")
    if q.student_id!=user.id:raise HTTPException(403,"Question not owned by student")
    r=Revision(question_id=q.id,student_id=user.id,text=data.text)
    db.add(r);db.flush()
    audit(db,user.id,q.course_id,"question_revised",q.material_id)
    db.commit()
    return {"revision_id":r.id,"question_id":q.id,"saved":True}

@app.get("/v1/courses/{course_id}/questions/mine")
def my_questions(course_id:str,db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"student")
    questions=db.query(Question).filter_by(course_id=course_id,student_id=user.id).order_by(Question.created_at).all()
    return {"items":[{"id":q.id,"text":q.text,"coaching":json.loads(q.feedback_json),
        "revisions":[{"id":r.id,"text":r.text} for r in db.query(Revision).filter_by(question_id=q.id,student_id=user.id).all()]} for q in questions]}


@app.get("/v1/courses/mine")
def my_courses(db:Session=Depends(session),user:User=Depends(actor)):
    memberships=db.query(Member).filter_by(user_id=user.id).all()
    courses=[]
    for m in memberships:
        c=db.get(Course,m.course_id)
        if c:
            courses.append({"id":c.id,"title":c.title,"role":m.role,
                "policy":c.policy,"teacher_approved":c.teacher_approved})
    return {"items":courses}

@app.get("/v1/courses/{course_id}/materials")
def course_materials(course_id:str,db:Session=Depends(session),user:User=Depends(actor)):
    membership=db.query(Member).filter_by(course_id=course_id,user_id=user.id).first()
    if not membership:
        raise HTTPException(403,"Course access denied")
    if not db.get(Course,course_id):
        raise HTTPException(404,"Course not found")
    materials=db.query(Material).filter_by(course_id=course_id).order_by(Material.created_at.desc()).all()
    # No raw document content, files, or teacher review evidence is returned.
    items=[]
    for m in materials:
        try:
            with private_store.material_workspace(m) as out:
                signed=nit.verified_count(out)
            status="FACULTY_SIGNED" if signed else "PENDING_FACULTY_REVIEW"
        except HTTPException as exc:
            if exc.status_code != 503:raise
            # A legacy local-path record must never block newer durable materials.
            status="UNAVAILABLE"
        items.append({"id":m.id,"filename":m.filename,"status":status})
    return {"items":items}


# Durable direct-upload and asynchronous job lifecycle.
app.include_router(ingest_router)
