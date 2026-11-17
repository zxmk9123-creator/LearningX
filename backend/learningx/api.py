"""LearningX MVP HTTP API. No unauthenticated data or evidence endpoints."""
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Literal
from fastapi import FastAPI, Depends, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from .storage import init_db, User, Course, Member, Material, Audit, Question, Revision
from .security import actor, session, require_course, secret
from . import engine_adapter as nit

app=FastAPI(title="LearningX NIT Backend",version="0.1.0",docs_url=None,redoc_url=None)
# Frontend origins must be explicitly configured; no wildcard CORS with bearer tokens.
origins=[x.strip() for x in os.getenv("NIT_CORS_ORIGINS","").split(",") if x.strip()]
if origins:
    app.add_middleware(CORSMiddleware,allow_origins=origins,allow_credentials=False,
        allow_methods=["GET","POST","PUT"],allow_headers=["Authorization","Content-Type"])

@app.on_event("startup")
def bootstrap():
    secret()
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
    filename=Path(file.filename or "").name
    ext=Path(filename).suffix.lower()
    if ext not in (".pdf",".pptx"):raise HTTPException(415,"PDF or PPTX only")
    data=await file.read(25*1024*1024+1)
    if len(data)>25*1024*1024:raise HTTPException(413,"File too large")
    if not data.startswith(b"%PDF-") and not (ext==".pptx" and data.startswith(b"PK")):
        raise HTTPException(422,"Invalid file signature")
    # Private filesystem only. No public static route for raw teacher materials.
    storage=Path(os.getenv("NIT_STORAGE_DIR","./private_lessons")).resolve()
    mid=os.urandom(16).hex()
    directory=storage/mid
    directory.mkdir(parents=True,exist_ok=False)
    try:
        original=directory/("source"+ext)
        original.write_bytes(data)
        sha=hashlib.sha256(data).hexdigest()
        out=directory/"parsed"
        document=nit.ingest(original,out)
        if document["source"]["sha256"]!=sha:
            raise HTTPException(422,"Parser source digest mismatch")
        record=Material(id=mid,course_id=course_id,filename=filename[:260],sha256=sha,
            out_dir=str(out),ingested_by=user.id)
        db.add(record)
        audit(db,user.id,course_id,"material_ingested",mid)
        db.commit()
        return {"id":mid,"filename":filename,"pages":len(document["pages"]),
            "status":"PENDING_FACULTY_REVIEW","approved_count":0}
    except Exception:
        db.rollback();shutil.rmtree(directory,ignore_errors=True)
        raise

@app.get("/v1/materials/{material_id}/candidates")
def list_candidates(material_id:str,db:Session=Depends(session),user:User=Depends(actor)):
    m=material_for(db,material_id)
    require_course(db,m.course_id,user,"faculty")
    return {"material_id":m.id,"eligible_candidates":nit.candidates(Path(m.out_dir)),
        "approved_count":nit.verified_count(Path(m.out_dir))}

@app.post("/v1/materials/{material_id}/approve")
def approve(material_id:str,data:Review,db:Session=Depends(session),user:User=Depends(actor)):
    m=material_for(db,material_id)
    require_course(db,m.course_id,user,"faculty")
    result=nit.approve_material(Path(m.out_dir),user.id,[x.model_dump() for x in data.items])
    audit(db,user.id,m.course_id,"evidence_signed",m.id,detail=f"count={result['approved']}")
    db.commit()
    return {"material_id":m.id,"approved_count":result["approved"],"status":"FACULTY_SIGNED"}

@app.post("/v1/courses/{course_id}/questions",status_code=201)
def ask(course_id:str,data:Ask,db:Session=Depends(session),user:User=Depends(actor)):
    require_course(db,course_id,user,"student")
    m=material_for(db,data.material_id,course_id)
    c=db.get(Course,course_id)
    if not c:raise HTTPException(404,"Course not found")
    result=nit.coach_question(Path(m.out_dir),user.id,data.question,c.policy,c.teacher_approved)
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
    return {"items":[{"id":m.id,"filename":m.filename,
        "status":"FACULTY_SIGNED" if nit.verified_count(Path(m.out_dir)) else "PENDING_FACULTY_REVIEW"} for m in materials]}
