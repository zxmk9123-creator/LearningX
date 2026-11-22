"""Strict adapter for the frozen NIT Lesson Intelligence v1.6.2 + pilot patch.

The engine is deliberately NOT reimplemented here. Install the reviewed engine
under NIT_ENGINE_ROOT. It provides scripts/ingest.py, scripts/verified_coaching.py,
runtime/coaching.py, runtime/gateway.py. No engine = ingestion/approval/coaching 503.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from fastapi import HTTPException

def root():
    # Prefer explicit, version-controlled path; fall back to reviewed vendored code.
    configured=os.getenv("NIT_ENGINE_ROOT","").strip()
    path=(Path(configured).expanduser() if configured
          else Path(__file__).resolve().parents[1]/"nit_engine")
    required=("scripts/ingest.py","scripts/parser_audit.py",
              "scripts/verified_coaching.py","scripts/review_auth.py",
              "scripts/text_normalization.py","scripts/verify_artifacts.py",
              "runtime/coaching.py","runtime/gateway.py")
    if not all((path/rel).is_file() for rel in required):
        raise HTTPException(503,"Frozen NIT engine is not fully installed")
    return path.resolve()

def _modules():
    p=root()
    if str(p) not in sys.path:sys.path.insert(0,str(p))
    if str(p/"scripts") not in sys.path:sys.path.insert(0,str(p/"scripts"))
    from scripts.verified_coaching import approve, verify, block_digest, load_blocks
    from runtime.coaching import run_coaching
    from runtime.gateway import OpenAICompatibleGateway
    return approve,verify,block_digest,load_blocks,run_coaching,OpenAICompatibleGateway

def level_rubric():
    """(rubric version, question normalizer) from the engine's level rules."""
    _modules()
    from runtime.level_rules import RUBRIC_VERSION, normalize
    return RUBRIC_VERSION,normalize

def coaching_policy_version():
    """Version of every root rule a coaching decision depends on (prompt, rubric, guardrails)."""
    _modules()
    from runtime.coaching import ROOT_POLICY_VERSION
    return ROOT_POLICY_VERSION

def model_gateway():
    """The configured model gateway, or None when no model is configured.

    Callers must keep a deterministic path for None: a missing model is a
    normal deployment state (guest classes, unconfigured pilots).
    """
    try:
        *_,Gateway=_modules()
        return Gateway()
    except Exception as exc:
        if exc.__class__.__name__=="GatewayFailure" or isinstance(exc,HTTPException):
            return None
        raise

def ingest(source:Path, out:Path):
    p=root()
    out.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,str(p/"scripts"/"ingest.py"),str(source),"--out",str(out),"--parser-mode","adaptive"]
    try:
        run=subprocess.run(command,timeout=180,capture_output=True,text=True,check=False)
    except subprocess.TimeoutExpired:
        raise HTTPException(504,"Document ingestion exceeded deadline")
    if run.returncode!=0 or not (out/"document.json").is_file():
        # Do not expose paths, raw source, or parser trace to remote callers.
        raise HTTPException(422,"Document could not be parsed")
    doc=json.loads((out/"document.json").read_text(encoding="utf-8"))
    if not isinstance(doc.get("pages"),list) or not doc.get("source",{}).get("sha256"):
        raise HTTPException(422,"Invalid canonical document")
    return doc

def candidates(out:Path):
    _,_,block_digest,load_blocks,_,_=_modules()
    from scripts.evidence_gate import is_block_candidate
    doc,blocks=load_blocks(out)
    result=[]
    for page in doc["pages"]:
        for block in page["blocks"]:
            try:
                eligible=(is_block_candidate(page,block,allow_legacy_missing=not doc.get("schema_version"))
                    and block.get("origin") in ("native","native_fallback")
                    and block.get("quality",{}).get("status")=="NATIVE_UNREVIEWED"
                    and block.get("quality",{}).get("eligible_for_coaching",False)
                    and not page.get("parser_selection",{}).get("coaching_hold"))
            except (KeyError,TypeError):
                eligible=False
            if eligible:
                result.append({"page":page["page"],"block_id":block["id"],
                    "block_sha256":block_digest(page["page"],block),
                    "role":block.get("content_role","unknown"),
                    "preview":str(block.get("text") or "")[:320]})
    return result

def approve_material(out:Path, reviewer:str, selection:list[dict]):
    approve,_,_,load_blocks,_,_=_modules()
    doc,_=load_blocks(out)
    signing=os.getenv("NIT_REVIEW_SIGNING_KEY","")
    if len(signing)<32:raise HTTPException(503,"Approval signing key not configured")
    review={"source_sha256":doc["source"]["sha256"],"reviewer_id":reviewer,
        "items":[{"page":x["page"],"block_id":x["block_id"],"block_sha256":x["block_sha256"],"decision":"approve"} for x in selection]}
    # Approved evidence is signed ONLY after server-authenticated faculty access.
    try:return approve(out,review,reviewer,signing)
    except (ValueError,PermissionError,KeyError) as exc:raise HTTPException(422,"Evidence approval rejected") from exc

def coach_question(out:Path, student:str, question:str, policy:str, teacher_approved:bool, *, use_model=True, objective=None):
    *_,run_coaching,Gateway=_modules()
    signing=os.getenv("NIT_REVIEW_SIGNING_KEY","")
    if len(signing)<32:raise HTTPException(503,"Approval verification key not configured")
    try:
        gateway=Gateway() if use_model else None
        if objective is None:
            return run_coaching(out,question,policy,teacher_approved,signing,student,gateway)
        return run_coaching(out,question,policy,teacher_approved,signing,student,gateway,objective=objective)
    except PermissionError as exc:raise HTTPException(409,"No usable faculty-signed evidence or course policy denies coaching") from exc
    except ValueError as exc:raise HTTPException(422,"Invalid question") from exc
    except Exception as exc:
        # Configuration failures should not be confused with coaching fallback.
        if exc.__class__.__name__=="GatewayFailure":
            raise HTTPException(503,"Model provider is not configured") from exc
        raise

def evidence_fingerprint(out:Path):
    """Digest of the faculty-signed evidence set; a re-approval changes it."""
    _,verify,*_=_modules()
    signing=os.getenv("NIT_REVIEW_SIGNING_KEY","")
    try:items=verify(out,signing) if len(signing)>=32 else []
    except (PermissionError,ValueError,FileNotFoundError,KeyError):items=[]
    ids=sorted(f"{int(i['page'])}:{i['block_id']}" for i in items)
    return hashlib.sha256("|".join(ids).encode()).hexdigest()[:32]

def verified_count(out:Path):
    _,verify,*_=_modules()
    signing=os.getenv("NIT_REVIEW_SIGNING_KEY","")
    if len(signing)<32:raise HTTPException(503,"Approval verification key not configured")
    try:return len(verify(out,signing))
    except (PermissionError,ValueError,FileNotFoundError,KeyError):
        return 0
