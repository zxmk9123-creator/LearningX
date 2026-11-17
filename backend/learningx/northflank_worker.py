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
    return process_next_job()


def run(stop_event=None, poll_interval=None):
    validate_config()
    stopped = stop_event or threading.Event()
    interval = poll_interval if poll_interval is not None else int(
        os.getenv("NIT_WORKER_POLL_SECONDS", "8"))
    interval = max(2, min(int(interval), 60))

    print("learningx_worker_started", flush=True)
    while not stopped.is_set():
        try:
            result = poll_once()
            state = result.get("result", "UNKNOWN")
            # Never log teacher documents, student questions, S3 URLs or tokens.
            if state not in ("EMPTY", "INFLIGHT"):
                print("learningx_worker_job " + str(state), flush=True)
            wait = interval if state in ("EMPTY", "INFLIGHT") else 2
        except Exception as exc:
            print("learningx_worker_retry " + type(exc).__name__, file=sys.stderr, flush=True)
            wait = max(interval, 10)
        stopped.wait(wait)
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
