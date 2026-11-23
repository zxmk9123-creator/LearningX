"""Bounded, opt-in enrichment; optional failures preserve native artifacts.

OCR and model interpretations never become signed coaching evidence here.
Each stage runs in an isolated copy, promoted only after a complete report.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile


def _limit(name, default, maximum):
    try:
        return max(1, min(int(os.getenv(name, str(default))), maximum))
    except ValueError:
        return default


def _jobs(out, kind):
    path = out / f"{kind}_jobs.jsonl"
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines()) if path.exists() else 0


def _stage(source, out, engine, kind):
    total = _jobs(out, kind)
    result = {"status": "NOT_RUN" if total else "NOT_REQUIRED", "total_jobs": total,
              "completed_jobs": 0, "verified_for_coaching": False}
    # OCR runs wherever its runtime is installed (the image ships Tesseract + Korean);
    # NIT_OCR_ENABLED=0 turns it off. Vision stays opt-in (it calls an outside provider).
    enabled = os.getenv(f"NIT_{kind.upper()}_ENABLED", "1" if kind == "ocr" else "")
    if not total or enabled != "1":
        return result
    if kind == "vision" and not os.getenv("GEMINI_API_KEY"):
        return dict(result, status="UNAVAILABLE", reason="PROVIDER_NOT_CONFIGURED")
    if kind == "ocr" and not shutil.which("tesseract"):
        return dict(result, status="UNAVAILABLE", reason="OCR_RUNTIME_MISSING")
    # A scanned lecture needs every page read: OCR defaults to the full cap.
    cap = _limit(f"NIT_{kind.upper()}_MAX_JOBS", 20 if kind == "ocr" else 3, 20)
    timeout = _limit(f"NIT_{kind.upper()}_TIMEOUT_SECONDS", 300 if kind == "ocr" else 120, 300)
    with tempfile.TemporaryDirectory(prefix="learningx-enrichment-") as td:
        staged = Path(td) / "parsed"
        shutil.copytree(out, staged)
        command = [sys.executable, str(engine / "scripts" / ("recover_ocr.py" if kind == "ocr" else "enrich_gemini.py")),
                   str(source), "--out", str(staged)]
        if kind == "ocr":
            command += ["--max-pages", str(cap), "--language", os.getenv("NIT_OCR_LANGUAGE", "kor+eng"),
                        "--dpi", str(_limit("NIT_OCR_DPI", 300, 400))]
        else:
            command += ["--kind", "vision", "--max-jobs", str(cap), "--workers", "1"]
        try:
            with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True) as run:
                try:
                    run.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(run.pid, signal.SIGKILL)
                    run.wait()
                    raise
            report_path = staged / ("ocr_report.json" if kind == "ocr" else "vision_execution_report.json")
            if run.returncode not in (0, 1) or not report_path.exists():
                return dict(result, status="FAILED", reason="STAGE_EXECUTION_FAILED")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if report.get("source_sha256") != json.loads((out / "document.json").read_text())["source"]["sha256"]:
                return dict(result, status="FAILED", reason="SOURCE_MISMATCH")
            document = json.loads((staged / "document.json").read_text(encoding="utf-8"))
            original = json.loads((out / "document.json").read_text(encoding="utf-8"))
            if document["source"]["sha256"] != original["source"]["sha256"]:
                return dict(result, status="FAILED", reason="SOURCE_MISMATCH")
            attempted = min(total, cap)
            failed = len(report.get("failed", []))
            completed = (sum(page.get("ocr_state") in ("complete", "empty") for page in document["pages"])
                         if kind == "ocr" else report.get("success", 0) + report.get("cached", 0))
            result.update(status="COMPLETE" if completed == total else "PARTIAL" if completed else "FAILED",
                          completed_jobs=completed, attempted_jobs=attempted, failed_jobs=failed,
                          remaining_jobs=max(0, total - completed))
            # No enrichment can set the native evidence signature or approval state.
            shutil.copytree(staged, out, dirs_exist_ok=True)
        except subprocess.TimeoutExpired:
            return dict(result, status="FAILED", reason="STAGE_TIMEOUT")
        except (OSError, ValueError, KeyError, TypeError):
            return dict(result, status="FAILED", reason="INVALID_STAGE_REPORT")
    return result


def enrich(source, out, engine):
    source, out, engine = Path(source), Path(out), Path(engine)
    stages = {kind: _stage(source, out, engine, kind) for kind in ("ocr", "vision")}
    document = json.loads((out / "document.json").read_text(encoding="utf-8"))
    document["deep_analysis"] = stages
    document["vision_execution"] = dict(stages["vision"], visual_semantics_verified=False)
    temporary = out / "document.json.tmp"
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(out / "document.json")
    (out / "deep_analysis_report.json").write_text(json.dumps(stages, indent=2), encoding="utf-8")
    return document
