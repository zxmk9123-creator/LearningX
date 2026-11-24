"""Always-on Northflank ingestion worker for the Neon-backed durable queue.

Run as a SECOND Northflank service using the same container image:
    python -m learningx.northflank_worker

Never expose this process as an HTTP service or pass credentials in arguments.
DB locks/leases and immutable object manifests provide at-least-once delivery.
"""
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import private_store


def validate_config():
    if os.getenv("NIT_WORKER_BACKEND") != "northflank_poll":
        raise RuntimeError("Set NIT_WORKER_BACKEND=northflank_poll")
    database = os.getenv("DATABASE_URL", "")
    if not (database.startswith("postgres://") or database.startswith("postgresql://")
            or database.startswith("postgresql+psycopg://")):
        raise RuntimeError("DATABASE_URL must be an external PostgreSQL connection")
    if not private_store.available():
        raise RuntimeError("Neon S3 environment variables are missing")
    root = Path(os.getenv("NIT_ENGINE_ROOT", "/app/nit_engine"))
    for required in ("scripts/ingest.py", "scripts/verified_coaching.py", "runtime/coaching.py"):
        if not (root / required).is_file():
            raise RuntimeError("Pinned NIT engine is incomplete")


def poll_once():
    # Import after runtime validation; storage.py establishes a DB pool on import.
    from .ingest_queue import process_next_job
    return process_next_job(prepare_live_preview=True, renew_lease=True)


def quiet_now(now=None):
    """True inside NIT_WORKER_QUIET_HOURS ("0-10" = 00:00 to 10:00, may wrap like "22-6").

    While quiet the worker makes no DB query, so the free-tier Neon compute can scale to zero.
    Clock is UTC+NIT_WORKER_TZ_OFFSET (default 9, KST). Unset or invalid means never quiet.
    """
    spec = os.getenv("NIT_WORKER_QUIET_HOURS", "").strip()
    try:
        start, end = (int(x) for x in spec.split("-"))
        if not (0 <= start <= 24 and 0 <= end <= 24) or start == end:
            return False
        offset = int(os.getenv("NIT_WORKER_TZ_OFFSET", "9"))
    except ValueError:
        return False
    hour = (now or datetime.now(timezone.utc)).astimezone(timezone(timedelta(hours=offset))).hour
    return start <= hour < end if start < end else hour >= start or hour < end


IDLE_AFTER_EMPTY_POLLS = 30  # ~1 minute with nothing to do, then poll less often


def idle_interval(base, empties):
    """Back off once the queue has stayed empty so an idle class room is not polled every second."""
    cap = max(base, min(int(os.getenv("NIT_WORKER_IDLE_POLL_SECONDS", "10")), 60))
    return cap if empties >= IDLE_AFTER_EMPTY_POLLS else base


def suitability_loop(stopped, interval=1):
    """Question suitability checks run beside ingestion so a long ingest never delays them."""
    from .suitability import poll_once as check_once
    print("learningx_suitability_worker_started", flush=True)
    empties = 0
    while not stopped.is_set():
        if quiet_now():
            stopped.wait(60)
            continue
        try:
            state = check_once()
            # Never log question text or model output; only the outcome.
            if state != "EMPTY":
                print("learningx_suitability " + state, flush=True)
            empties = empties + 1 if state == "EMPTY" else 0
            wait = min(idle_interval(interval, empties), 3) if state == "EMPTY" else 0
        except Exception as exc:
            print("learningx_suitability_retry " + type(exc).__name__, file=sys.stderr, flush=True)
            wait = 10
        stopped.wait(wait)


def run(stop_event=None, poll_interval=None):
    validate_config()
    stopped = stop_event or threading.Event()
    checker = threading.Thread(target=suitability_loop, args=(stopped,), name="suitability", daemon=True)
    checker.start()
    interval = poll_interval if poll_interval is not None else int(
        os.getenv("NIT_WORKER_POLL_SECONDS", "2"))
    interval = max(2, min(int(interval), 60))

    print("learningx_worker_started", flush=True)
    empties = 0
    while not stopped.is_set():
        if quiet_now():
            stopped.wait(60)
            continue
        try:
            result = poll_once()
            state = result.get("result", "UNKNOWN")
            # Never log teacher documents, student questions, S3 URLs or tokens.
            if state not in ("EMPTY", "INFLIGHT"):
                print("learningx_worker_job " + str(state), flush=True)
            empties = empties + 1 if state == "EMPTY" else 0
            wait = idle_interval(interval, empties) if state in ("EMPTY", "INFLIGHT") else 2
        except Exception as exc:
            print("learningx_worker_retry " + type(exc).__name__, file=sys.stderr, flush=True)
            wait = max(interval, 10)
        stopped.wait(wait)
    checker.join(timeout=15)
    print("learningx_worker_stopped", flush=True)


def main():
    stopped = threading.Event()

    def on_signal(_signum, _frame):
        stopped.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        run(stopped)
    except (RuntimeError, ValueError) as exc:
        # Configuration error strings contain only key names, never secret values.
        print("learningx_worker_not_ready " + str(exc), file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
