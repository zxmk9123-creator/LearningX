"""Private, durable lesson artifacts on Neon S3-compatible Object Storage.

Only the API server has storage credentials. Every request downloads the
canonical document + signed faculty decision to an isolated scratch workspace.
Object data is never used as code or exposed as public URLs.
"""
import hashlib
import json
import mimetypes
import os
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath
import re
import tempfile

import boto3
from botocore.config import Config
from fastapi import HTTPException

REQUIRED = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3",
            "AWS_REGION", "NIT_STORAGE_BUCKET")
MAX_FILES = 300
MAX_TOTAL_BYTES = 180 * 1024 * 1024
MAX_MANIFEST_BYTES = 200 * 1024
MATERIAL_ID = re.compile(r"^[0-9a-f]{32}$")


def available():
    """An incomplete configuration never falls back to serverless local storage."""
    return all(os.getenv(key, "").strip() for key in REQUIRED)


def serverless():
    return (os.getenv("NIT_SERVERLESS_RUNTIME") == "1" or os.getenv("VERCEL") == "1"
            or os.getenv("NIT_HOSTED_RUNTIME") == "1")


def require_store():
    if not available():
        raise HTTPException(503, "Private durable lesson storage is not configured")
    return PrivateArtifactStore()


def _safe_name(value):
    name = PurePosixPath(value)
    if (name.is_absolute() or not name.parts or ".." in name.parts
            or any(p in ("", ".") for p in name.parts)
            or "\\" in value or len(value) > 450):
        raise HTTPException(503, "Invalid private artifact manifest")
    s = str(name)
    if not (s.startswith("parsed/") or s in ("source.pdf", "source.pptx")):
        raise HTTPException(503, "Invalid private artifact name")
    return s


def _sha(data):
    return hashlib.sha256(data).hexdigest()


class PrivateArtifactStore:
    def __init__(self, client=None):
        self.bucket = os.environ["NIT_STORAGE_BUCKET"]
        self.client = client or boto3.client(
            "s3",
            endpoint_url=os.environ["AWS_ENDPOINT_URL_S3"],
            region_name=os.environ["AWS_REGION"],
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    def prefix(self, course_id, material_id):
        if not MATERIAL_ID.fullmatch(course_id) or not MATERIAL_ID.fullmatch(material_id):
            raise HTTPException(503, "Invalid material storage identity")
        return f"materials/{course_id}/{material_id}"

    def ref(self, prefix):
        return f"s3://{self.bucket}/{prefix}"

    def parse_ref(self, record):
        prefix = self.prefix(record.course_id, record.id)
        if record.out_dir != self.ref(prefix):
            raise HTTPException(503, "Invalid material storage reference")
        return prefix

    def _put(self, key, data, content_type):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=content_type)

    def _get(self, key, max_bytes):
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        raw = body.read(max_bytes + 1)
        body.close()
        if len(raw) > max_bytes:
            raise HTTPException(503, "Private artifact exceeds permitted size")
        return raw

    def _manifest(self, prefix):
        try:
            raw = self._get(prefix + "/manifest.json", MAX_MANIFEST_BYTES)
            doc = json.loads(raw)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Private artifact manifest unavailable") from exc
        if doc.get("schema") != "learningx-artifacts-v1" or not isinstance(doc.get("files"), list):
            raise HTTPException(503, "Invalid private artifact manifest")
        files = doc["files"]
        if not 1 <= len(files) <= MAX_FILES:
            raise HTTPException(503, "Invalid private artifact count")
        seen = set()
        total = 0
        for item in files:
            try:
                relative = _safe_name(item["path"])
                key = item["key"]
                size = item["size"]
                digest = item["sha256"]
                assert isinstance(key, str) and key.startswith(prefix + "/")
                assert isinstance(size, int) and 0 <= size <= MAX_TOTAL_BYTES
                assert re.fullmatch(r"[0-9a-f]{64}", digest)
            except (AssertionError, TypeError, KeyError, ValueError):
                raise HTTPException(503, "Invalid private artifact manifest") from None
            if relative in seen:
                raise HTTPException(503, "Duplicate private artifact name")
            seen.add(relative)
            total += size
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(503, "Private artifact bundle too large")
        return doc

    def _save_manifest(self, prefix, manifest):
        body = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode()
        if len(body) > MAX_MANIFEST_BYTES:
            raise HTTPException(503, "Private artifact manifest too large")
        self._put(prefix + "/manifest.json", body, "application/json")

    def _garbage_collect_partial(self, keys):
        # On first-commit failures only. Never delete a previously signed ref.
        for key in keys:
            try:
                self.client.delete_object(Bucket=self.bucket, Key=key)
            except Exception:
                pass

    def publish_new(self, folder: Path, course_id: str, material_id: str):
        """Publish immutable files, then commit the manifest as the last object."""
        prefix = self.prefix(course_id, material_id)
        entries = []
        uploaded = []
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        if len(files) < 2 or len(files) > MAX_FILES:
            raise HTTPException(503, "Invalid parsed material file count")
        total = 0
        try:
            for path in files:
                relative = _safe_name(path.relative_to(folder).as_posix())
                if path.is_symlink():
                    raise HTTPException(503, "Symlink in parsed material")
                raw = path.read_bytes()
                total += len(raw)
                if total > MAX_TOTAL_BYTES:
                    raise HTTPException(503, "Material exceeds private storage limit")
                key = prefix + "/files/" + relative
                kind = mimetypes.guess_type(relative)[0] or "application/octet-stream"
                self._put(key, raw, kind)
                uploaded.append(key)
                entries.append({"path": relative, "key": key, "size": len(raw),
                                "sha256": _sha(raw)})
            if not any(x["path"].startswith("source.") for x in entries) or not any(x["path"] == "parsed/document.json" for x in entries):
                raise HTTPException(503, "Missing required canonical artifacts")
            manifest = {"schema": "learningx-artifacts-v1", "files": entries}
            self._save_manifest(prefix, manifest)
            return self.ref(prefix)
        except Exception:
            self._garbage_collect_partial(uploaded)
            raise

    @contextmanager
    def workspace(self, record):
        """Verify all immutable artifacts before letting NIT read any of them."""
        prefix = self.parse_ref(record)
        manifest = self._manifest(prefix)
        with tempfile.TemporaryDirectory(prefix="learningx-private-") as folder:
            root = Path(folder)
            for item in manifest["files"]:
                rel = _safe_name(item["path"])
                raw = self._get(item["key"], item["size"])
                if len(raw) != item["size"] or _sha(raw) != item["sha256"]:
                    raise HTTPException(503, "Private artifact integrity check failed")
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            yield root / "parsed"

    def publish_signed(self, record, parsed: Path):
        """Commit a new immutable signed-evidence version, then flip manifest."""
        prefix = self.parse_ref(record)
        signed = parsed / "approved_evidence.json"
        if not signed.is_file():
            raise HTTPException(503, "No faculty signed evidence was generated")
        raw = signed.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES * 10:
            raise HTTPException(503, "Signed evidence is too large")
        digest = _sha(raw)
        key = prefix + "/signed/" + digest + ".json"
        self._put(key, raw, "application/json")
        manifest = self._manifest(prefix)
        entry = {"path": "parsed/approved_evidence.json", "key": key,
                 "size": len(raw), "sha256": digest}
        manifest["files"] = [x for x in manifest["files"] if x["path"] != entry["path"]] + [entry]
        self._save_manifest(prefix, manifest)


@contextmanager
def material_workspace(record):
    """Resolve only authenticated, DB-selected material refs."""
    if record.out_dir.startswith("s3://"):
        with require_store().workspace(record) as path:
            yield path
    elif serverless():
        # Even if an old DB row contains a /tmp path, never read it on Vercel.
        raise HTTPException(503, "Durable material migration required")
    else:
        with nullcontext(Path(record.out_dir)) as path:
            yield path


def durable_upload_enabled():
    return available()


def store_after_ingest(folder: Path, course_id: str, material_id: str):
    return require_store().publish_new(folder, course_id, material_id)


def persist_approval(record, parsed: Path):
    if record.out_dir.startswith("s3://"):
        require_store().publish_signed(record, parsed)
