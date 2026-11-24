"""Private, durable lesson artifacts on Neon S3-compatible Object Storage.

Only the API server has storage credentials. Every request downloads the
canonical document + signed faculty decision to an isolated scratch workspace.
Object data is never used as code or exposed as public URLs.
"""
import hashlib
import json
import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from pathlib import Path, PurePosixPath
import re
import tempfile

import boto3
from botocore.config import Config
from fastapi import HTTPException

REQUIRED = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ENDPOINT_URL_S3",
            "AWS_REGION", "NIT_STORAGE_BUCKET")
PDF_UPLOAD_LIMIT = 60 * 1024 * 1024
PPTX_UPLOAD_LIMIT = 40 * 1024 * 1024
MAX_FILES = 300
MAX_TOTAL_BYTES = 180 * 1024 * 1024
MAX_MANIFEST_BYTES = 200 * 1024
MATERIAL_ID = re.compile(r"^[0-9a-f]{32}$")

# Verified artifacts are immutable and named by their SHA-256, so a local copy can be
# reused for every later request. Without it each coaching call, material list and
# approval downloaded the whole lesson bundle (source PDF included) again, which used up
# the storage provider's monthly network-transfer allowance.
CACHE_DIR = Path(os.getenv("NIT_ARTIFACT_CACHE_DIR", os.path.join(tempfile.gettempdir(), "learningx-artifact-cache")))
CACHE_MAX_BYTES = int(os.getenv("NIT_ARTIFACT_CACHE_BYTES", str(512 * 1024 * 1024)))


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


def _cache_path(digest):
    return CACHE_DIR / digest[:2] / digest


def _cache_store(digest, raw):
    """Keep a verified artifact by digest; drop the least recently used beyond the cap."""
    if CACHE_MAX_BYTES <= 0 or len(raw) > CACHE_MAX_BYTES // 4:
        return
    try:
        target = _cache_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=target.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
        os.replace(tmp, target)
        entries = sorted((p.stat().st_mtime, p.stat().st_size, p) for p in CACHE_DIR.glob("*/*") if p.is_file())
        total = sum(size for _, size, _ in entries)
        for _, size, path in entries:
            if total <= CACHE_MAX_BYTES:
                break
            path.unlink(missing_ok=True)
            total -= size
    except OSError:
        pass  # The cache only saves transfer; a full or read-only disk just skips it.


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

    def _get_verified(self, key, size, digest):
        """One immutable artifact, from the local cache when its digest is already there."""
        cached = _cache_path(digest)
        try:
            if cached.stat().st_size == size:
                raw = cached.read_bytes()
                if len(raw) == size:
                    os.utime(cached)
                    return raw
        except OSError:
            pass
        raw = self._get(key, size)
        if len(raw) != size or _sha(raw) != digest:
            raise HTTPException(503, "Private artifact integrity check failed")
        _cache_store(digest, raw)
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
        files = sorted(p for p in folder.rglob("*") if p.is_file())
        if len(files) < 2 or len(files) > MAX_FILES:
            raise HTTPException(503, "Invalid parsed material file count")
        artifacts = []
        for path in files:
            if path.is_symlink():
                raise HTTPException(503, "Symlink in parsed material")
            relative = _safe_name(path.relative_to(folder).as_posix())
            artifacts.append((path, relative, path.stat().st_size))
        names = {relative for _, relative, _ in artifacts}
        if not names.intersection({"source.pdf", "source.pptx"}) or "parsed/document.json" not in names:
            raise HTTPException(503, "Missing required canonical artifacts")
        if sum(size for _, _, size in artifacts) > MAX_TOTAL_BYTES:
            raise HTTPException(503, "Material exceeds private storage limit")

        def upload(artifact):
            path, relative, size = artifact
            key = prefix + "/files/" + relative
            kind = mimetypes.guess_type(relative)[0] or "application/octet-stream"
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                stream.seek(0)
                self._put(key, stream, kind)
            return {"path": relative, "key": key, "size": size, "sha256": digest}

        try:
            # Bound concurrent uploads; streams avoid extra copies of large files.
            # The manifest is published only after every upload has completed.
            with ThreadPoolExecutor(max_workers=3) as pool:
                entries = list(pool.map(upload, artifacts))
            self._save_manifest(prefix, {"schema": "learningx-artifacts-v1", "files": entries})
        except Exception:
            self._garbage_collect_partial([prefix + "/files/" + name for name in names])
            raise
        return self.ref(prefix)

    @contextmanager
    def workspace(self, record):
        """Verify all immutable artifacts before letting NIT read any of them."""
        prefix = self.parse_ref(record)
        manifest = self._manifest(prefix)
        with tempfile.TemporaryDirectory(prefix="learningx-private-") as folder:
            root = Path(folder)
            for item in manifest["files"]:
                rel = _safe_name(item["path"])
                if rel in ("parsed/live-preview.pdf", "parsed/live-text.pdf") or rel.startswith("parsed/live-pages/"):
                    continue
                raw = self._get_verified(item["key"], item["size"], item["sha256"])
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


def read_material_artifact(record, name, limit):
    """Read one verified immutable artifact without downloading the lesson bundle."""
    name = _safe_name(name)
    if record.out_dir.startswith("s3://"):
        store = require_store()
        prefix = store.parse_ref(record)
        for item in store._manifest(prefix)["files"]:
            if item["path"] != name:
                continue
            if item["size"] > limit:
                raise HTTPException(413, "Artifact is too large")
            return store._get_verified(item["key"], item["size"], item["sha256"])
    else:
        with material_workspace(record) as out:
            path = out.parent / name if name.startswith("source.") else out / name.removeprefix("parsed/")
            if path.is_file():
                with path.open("rb") as handle:
                    raw = handle.read(limit + 1)
                if len(raw) > limit:
                    raise HTTPException(413, "Artifact is too large")
                return raw
    raise HTTPException(404, "Artifact not found")



def has_material_artifact(record, name):
    name = _safe_name(name)
    if record.out_dir.startswith("s3://"):
        store = require_store()
        return any(item["path"] == name for item in store._manifest(store.parse_ref(record))["files"])
    with material_workspace(record) as out:
        return (out / name.removeprefix("parsed/")).is_file()
