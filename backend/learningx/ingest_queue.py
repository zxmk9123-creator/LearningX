"""Direct-to-Neon upload contract and DB-backed asynchronous parser work.

P0: large bodies NEVER pass through Vercel Functions.
The worker endpoint processes ONE leased DB job per invocation. Dispatch is
external (durable queue/worker runner) and must be configured separately.
"""
import hashlib
import hmac
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import tempfile
from threading import Event, Thread
from time import monotonic

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from .storage import Course, IngestJob, Material, SessionLocal, User
from .guest import is_guest
from .security import actor, require_course, session
from . import engine_adapter as nit, private_store

logger = logging.getLogger(__name__)
router=APIRouter(prefix="/v1")
MAX_UPLOAD_BYTES=100 * 1024 * 1024  # 32.9 MB PPTX supported; can lift only after budget tests.
UPLOAD_TTL_SECONDS=900
JOB_LEASE_SECONDS=3600  # Dedicated GitHub Actions process, with stale-lease recovery
MAX_ATTEMPTS=3
WORKER_LEASE_SECONDS=90
HEARTBEAT_SECONDS=20
MIME={".pdf":"application/pdf",
      ".pptx":"application/vnd.openxmlformats-officedocument.presentationml.presentation"}


class UploadRequest(BaseModel):
    filename:str=Field(min_length=5,max_length=260)
    size_bytes:int=Field(ge=1,le=MAX_UPLOAD_BYTES)


def _job_or_404(db,job_id,course_id):
    row=db.get(IngestJob,job_id)
    if not row or row.course_id!=course_id:
        raise HTTPException(404,"Upload not found")
    return row


def _public_job(job):
    return {"upload_id":job.id,"status":job.status,"material_id":job.material_id,
            "attempts":job.attempts,"error_code":job.error_code,
            "analysis_coverage":json.loads(job.coverage_json) if job.coverage_json else None}



@router.post("/courses/{course_id}/uploads/initiate",status_code=201)
def initiate_upload(course_id:str,body:UploadRequest,
                    db:Session=Depends(session),user=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    if not db.get(Course,course_id):raise HTTPException(404,"Course not found")
    suffix=Path(body.filename).suffix.lower()
    if suffix not in MIME:raise HTTPException(415,"PDF/PPTX only")
    guest = is_guest(user)
    job = None
    if guest:
        limit = private_store.PDF_UPLOAD_LIMIT if suffix == ".pdf" else private_store.PPTX_UPLOAD_LIMIT
        if body.size_bytes > limit:
            raise HTTPException(413, f"Guest upload limit is {limit // (1024 * 1024)} MiB")
        db.query(User).filter_by(id=user.id).with_for_update().one()
        job = db.query(IngestJob).filter_by(uploaded_by=user.id).first()
        if job and (job.status != "AWAITING_UPLOAD" or job.course_id != course_id
                    or job.filename != Path(body.filename).name or job.expected_size != body.size_bytes):
            raise HTTPException(409, "Guest sessions can upload one lesson")
    store=private_store.require_store()
    if job is None:
        job=IngestJob(id=os.urandom(16).hex(),course_id=course_id,uploaded_by=user.id,
                      filename=Path(body.filename).name[:260],
                      media_type=MIME[suffix],expected_size=body.size_bytes,
                      source_key="pending")
        # Generate the unique source key before the INSERT/flush.
        job.source_key=f"incoming/{course_id}/{job.id}/source{suffix}"
        db.add(job)
    try:
        url=store.client.generate_presigned_url(
            ClientMethod="put_object",
            Params={"Bucket":store.bucket,"Key":job.source_key,
                    "ContentType":job.media_type,
                    **({"ContentLength":body.size_bytes} if guest else {})},
            ExpiresIn=UPLOAD_TTL_SECONDS,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(503,"Unable to issue private upload URL") from exc
    return {"upload_id":job.id,"upload_url":url,"method":"PUT",
            "headers":{"Content-Type":job.media_type},
            "expires_in_seconds":UPLOAD_TTL_SECONDS,
            "max_size_bytes":MAX_UPLOAD_BYTES,
            "next":"POST /v1/courses/{course_id}/uploads/{upload_id}/complete"}


async def _dispatch_job(job_id:str,attempt:int):
    """Dispatch one Neon DB job to a standalone GitHub Actions worker.

    Never send file contents to GitHub; the DB remains source of truth.
    Scheduled recovery polls queued jobs if the trigger is lost.
    """
    mode=os.getenv("NIT_WORKER_BACKEND","")
    if mode=="northflank_poll":
        # A separate always-on worker polls queued DB jobs. The committed row
        # itself is the durable dispatch event; no GitHub/Vercel credentials.
        return
    if mode in ("vercel_queue","vercel_queues") or (not mode and os.getenv("NIT_QUEUE_ENABLED")=="1"):
        try:
            from vercel.queue import send
            await send("learningx-ingest", {"job_id":job_id},
                       idempotency_key=f"learningx-{job_id}-attempt-{attempt}")
            return
        except Exception as exc:
            raise HTTPException(503,"Ingestion event could not be published; retry completion") from exc
    if mode!="github_actions":
        raise HTTPException(503,"Dedicated ingestion worker is not configured")
    import urllib.request
    import re
    repo=os.getenv("NIT_WORKER_REPOSITORY","zxmk9123-creator/LearningX")
    token=os.getenv("NIT_WORKER_GITHUB_TOKEN","")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",repo) or len(token)<20:
        raise HTTPException(503,"GitHub Actions worker dispatch credentials missing")
    request=urllib.request.Request(
        f"https://api.github.com/repos/{repo}/dispatches",
        data=json.dumps({"event_type":"learningx-ingest",
                         "client_payload":{"job_id":job_id}}).encode(),
        headers={"Authorization":"Bearer "+token,
                 "Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "Content-Type":"application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request,timeout=8) as response:
            if response.status!=204:
                raise RuntimeError("Dispatch did not return HTTP 204")
    except Exception as exc:
        raise HTTPException(503,"Ingestion dispatch delayed; retry later") from exc

@router.post("/courses/{course_id}/uploads/{upload_id}/complete",status_code=202)
async def complete_upload(course_id:str,upload_id:str,db:Session=Depends(session),user=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    job=_job_or_404(db,upload_id,course_id)
    if job.status=="QUEUED":
        # Retrying the same request re-publishes any event lost before ack.
        await _dispatch_job(job.id,job.attempts)
        return _public_job(job)
    if job.status in ("RUNNING","READY"):
        return _public_job(job)
    if job.status!="AWAITING_UPLOAD":
        raise HTTPException(409,"Upload is not awaiting completion")
    ts=job.created_at
    if ts.tzinfo is None:ts=ts.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc)>ts+timedelta(hours=1):
        raise HTTPException(410,"Upload session expired")
    store=private_store.require_store()
    try:
        head=store.client.head_object(Bucket=store.bucket,Key=job.source_key)
    except ClientError as exc:
        raise HTTPException(409,"Upload not found in private storage") from exc
    length=head.get("ContentLength")
    if (not isinstance(length,int) or length<=0 or
            length!=job.expected_size or length>MAX_UPLOAD_BYTES):
        raise HTTPException(422,"Uploaded byte size differs from declared size")
    # HEAD content type can differ between S3 providers; verify on actual parsing.
    job.status="QUEUED"
    db.commit()
    await _dispatch_job(job.id,job.attempts)
    return _public_job(job)


@router.get("/courses/{course_id}/uploads/{upload_id}")
def upload_status(course_id:str,upload_id:str,db:Session=Depends(session),user=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    return _public_job(_job_or_404(db,upload_id,course_id))


@router.post("/courses/{course_id}/uploads/{upload_id}/retry",status_code=202)
async def retry_upload(course_id:str,upload_id:str,db:Session=Depends(session),user=Depends(actor)):
    require_course(db,course_id,user,"faculty")
    row=_job_or_404(db,upload_id,course_id)
    if row.status!="FAILED" or row.attempts>=MAX_ATTEMPTS:
        raise HTTPException(409,"Upload is not retryable")
    row.status="QUEUED"
    row.error_code=None
    db.commit()
    await _dispatch_job(row.id,row.attempts)
    return _public_job(row)


def _claim(db, job_id=None, *, lease_seconds=JOB_LEASE_SECONDS):
    now=datetime.now(timezone.utc)
    # Polling without a job ID must also close exhausted, expired leases.
    expired = db.query(IngestJob).filter(
        IngestJob.status == "RUNNING",
        IngestJob.lease_until < now,
        IngestJob.attempts >= MAX_ATTEMPTS,
    )
    if job_id is not None:
        expired = expired.filter(IngestJob.id == job_id)
    for job in expired.with_for_update(skip_locked=True):
        job.status = "FAILED"
        job.error_code = "WORKER_TIMEOUT"
        job.lease_until = None
        _log_ingest(job.id, job.attempts, "claim", "exhausted")
    db.commit()
    query=db.query(IngestJob)
    if job_id is not None:
        query=query.filter(IngestJob.id==job_id)
    row=(query
         .filter(or_(IngestJob.status=="QUEUED",
                    (IngestJob.status=="RUNNING")&(IngestJob.lease_until<now)))
         .filter(IngestJob.attempts<MAX_ATTEMPTS)
         .order_by(IngestJob.created_at)
         .with_for_update(skip_locked=True).first())
    if not row:return None
    queued_at = row.updated_at
    if queued_at.tzinfo is None:
        queued_at = queued_at.replace(tzinfo=timezone.utc)
    wait_ms = max(0, round((now - queued_at).total_seconds() * 1000))
    recovered = row.status == "RUNNING"
    row.status = "RUNNING"
    row.attempts += 1
    row.lease_until = now + timedelta(seconds=lease_seconds)
    db.commit()
    _log_ingest(row.id, row.attempts, "claim", "recovered" if recovered else "start", wait_ms)

    return row.id, row.attempts


def _download_verified(store,job,path):
    hasher=hashlib.sha256()
    total=0
    response=store.client.get_object(Bucket=store.bucket,Key=job.source_key)
    body=response["Body"]
    try:
        with path.open("wb") as f:
            for chunk in body.iter_chunks(chunk_size=1024*1024):
                total+=len(chunk)
                if total>job.expected_size or total>MAX_UPLOAD_BYTES:
                    raise ValueError("Oversized upload")
                hasher.update(chunk)
                f.write(chunk)
    finally:
        body.close()
    if total!=job.expected_size:
        raise ValueError("Upload byte count mismatch")
    with path.open("rb") as f: magic=f.read(8)
    if path.suffix==".pdf" and not magic.startswith(b"%PDF-"):
        raise ValueError("Invalid PDF magic")
    if path.suffix==".pptx" and not magic.startswith(b"PK"):
        raise ValueError("Invalid PPTX magic")
    return hasher.hexdigest()


def _log_ingest(job_id, attempt, stage, event, elapsed_ms=None):
    data = {"job_id": job_id, "attempt": attempt, "stage": stage, "event": event}
    if elapsed_ms is not None:
        data["elapsed_ms"] = elapsed_ms
    print("learningx_ingest " + json.dumps(data, separators=(",", ":")), flush=True)


def _owned_job(db, job_id, attempt):
    return (db.query(IngestJob).filter_by(id=job_id, attempts=attempt, status="RUNNING")
            .filter(IngestJob.lease_until > datetime.now(timezone.utc))
            .with_for_update().populate_existing().first())


def _renew_lease(job_id, attempt):
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        count = db.query(IngestJob).filter_by(id=job_id, attempts=attempt, status="RUNNING").filter(
            IngestJob.lease_until > now
        ).update({"lease_until": now + timedelta(seconds=WORKER_LEASE_SECONDS)}, synchronize_session=False)
        db.commit()
        return count == 1


@contextmanager
def _heartbeat(job_id, attempt, enabled):
    if not enabled:
        yield
        return
    stop = Event()

    def renew():
        while not stop.wait(HEARTBEAT_SECONDS):
            try:
                if not _renew_lease(job_id, attempt):
                    return
            except Exception as exc:
                logger.warning("learningx_ingest_heartbeat_failed job=%s attempt=%s exception=%s",
                               job_id, attempt, type(exc).__name__)

    thread = Thread(target=renew, name="ingest-heartbeat", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1)


def process_next_job(job_id=None, *, prepare_live_preview=False, renew_lease=False):
    """Perform one leased job by ID; repeated queue messages are idempotent."""
    with SessionLocal() as db:
        claimed=_claim(db, job_id, lease_seconds=WORKER_LEASE_SECONDS if renew_lease else JOB_LEASE_SECONDS)
        if not claimed:
            row=db.get(IngestJob,job_id) if job_id else None
            if row and row.status=="READY":return {"result":"ALREADY_READY","job_id":job_id}
            if row and row.status=="FAILED":return {"result":"ALREADY_FAILED","job_id":job_id}
            if row and row.status=="RUNNING":
                return {"result":"INFLIGHT","job_id":job_id}
            return {"result":"EMPTY" if not row else "ERROR","job_id":job_id}
        claimed, attempt = claimed
    with _heartbeat(claimed, attempt, renew_lease):
        return _run_claimed_job(claimed, attempt, prepare_live_preview)


def _run_claimed_job(claimed, attempt, prepare_live_preview):
    with SessionLocal() as db:
        row = db.get(IngestJob, claimed)
        if not row or row.attempts != attempt or row.status != "RUNNING":
            return {"result": "LEASE_LOST", "job_id": claimed}
        # Keep the row detached during long work so the heartbeat owns its
        # own short DB transactions and the worker holds no idle connection.
        db.expunge(row)
        db.rollback()
        stage = "storage_init"
        started = monotonic()
        _log_ingest(claimed, attempt, stage, "start")

        def advance(next_stage):
            nonlocal stage, started
            _log_ingest(claimed, attempt, stage, "done", round((monotonic() - started) * 1000))
            stage, started = next_stage, monotonic()
            _log_ingest(claimed, attempt, stage, "start")

        try:
            store=private_store.require_store()
            with tempfile.TemporaryDirectory(prefix="learningx-worker-") as td:
                root=Path(td)
                ext=Path(row.filename).suffix.lower()
                original=root/("source"+ext)
                advance("download")
                digest=_download_verified(store,row,original)
                parsed=root/"parsed"
                advance("parse")
                document=nit.ingest(original,parsed)
                if document["source"]["sha256"]!=digest:
                    raise ValueError("Parser source digest mismatch")
                advance("optional_analysis")
                from .deep_analysis import enrich
                document = enrich(original, parsed, nit.root())
                preview_ready = None
                if ext == ".pptx" and prepare_live_preview:
                    from .pptx_preview import generate_preview
                    advance("live_preview")
                    preview_ready = generate_preview(original, parsed, len(document["pages"]))
                    if preview_ready:
                        artifacts = [path for path in root.rglob("*") if path.is_file()]
                        if (len(artifacts) > private_store.MAX_FILES
                                or sum(path.stat().st_size for path in artifacts) > private_store.MAX_TOTAL_BYTES):
                            (parsed / "live-preview.pdf").unlink()
                            preview_ready = False
                    if not preview_ready:
                        logger.warning("learningx_live_preview_unavailable")
                prepared_pages = 0
                if prepare_live_preview and (ext == ".pdf" or preview_ready):
                    from .live_slides import prepare_live_pages
                    advance("live_pages")
                    repaired = parsed / nit.LIVE_TEXT
                    source_pdf = ((repaired if repaired.is_file() else original) if ext == ".pdf"
                                  else parsed / "live-preview.pdf")
                    try:
                        prepared_pages = prepare_live_pages(root, source_pdf, document["pages"])
                    except Exception:
                        shutil.rmtree(parsed / "live-pages", ignore_errors=True)
                        logger.warning("learningx_live_pages_unavailable")
                advance("publish")
                # The row lock fences storage publication as all attempts
                # write the same immutable-material prefix.
                row = _owned_job(db, claimed, attempt)
                if not row:
                    return {"result": "LEASE_LOST", "job_id": claimed}
                location=store.publish_new(root,row.course_id,row.id)
                advance("database_commit")
                coverage=dict(document.get("media_coverage",{}))
                coverage["native_extraction"] = "COMPLETE"
                coverage["vision"] = document.get("vision_execution", {
                    "status": "UNKNOWN", "completed_jobs": 0,
                })
                coverage["ocr"] = document["deep_analysis"]["ocr"]
                coverage["semantic_analysis"] = "NOT_RUN"
                coverage["faculty_approval_required"] = True
                coverage["live_pages_prepared"] = prepared_pages
                if ext==".pptx":
                    coverage["live_preview"] = "READY" if preview_ready else "UNAVAILABLE"
                    coverage.setdefault("audio_status","UNKNOWN_NOT_VERIFIED")
                    coverage.setdefault("warning","PPTX narration was not transcribed")
                # Never attach an unverified parsed document to a different course.
                item=db.get(Material,row.id)
                if item is None:
                    item=Material(id=row.id,course_id=row.course_id,
                        filename=row.filename,sha256=digest,
                        out_dir=location,ingested_by=row.uploaded_by)
                    db.add(item)
                row.material_id=row.id
                row.file_sha256=digest
                row.coverage_json=json.dumps(coverage,ensure_ascii=False)
                row.status="READY"
                row.error_code=None
                row.lease_until=None
                db.commit()
                _log_ingest(claimed, attempt, stage, "done", round((monotonic() - started) * 1000))
                return {"result":"READY","job_id":claimed}
        except Exception as exc:
            _log_ingest(claimed, attempt, stage, "failed", round((monotonic() - started) * 1000))
            logger.warning("learningx_ingest_failed job=%s attempt=%s stage=%s exception=%s",
                           claimed, attempt, stage, type(exc).__name__)
            db.rollback()
            row = _owned_job(db, claimed, attempt)
            if row:
                # No raw slide text, stack trace, secrets or filenames in status.
                row.status="FAILED"
                row.error_code=("ENGINE_UNAVAILABLE" if isinstance(exc,HTTPException)
                                and exc.status_code==503 else "INGEST_FAILED")
                row.lease_until=None
                db.commit()
            return {"result":"FAILED" if row else "LEASE_LOST","job_id":claimed}


@router.post("/internal/ingest/process")
def process_worker(authorization:str|None=Header(default=None)):
    """Service-to-service call ONLY. Never accepts faculty/student JWTs."""
    key=os.getenv("NIT_WORKER_SECRET","")
    if len(key)<32:
        raise HTTPException(503,"Ingestion worker is not configured")
    supplied=(authorization or "").removeprefix("Bearer ")
    if not hmac.compare_digest(supplied,key):
        raise HTTPException(401,"Worker authentication required")
    return process_next_job()
