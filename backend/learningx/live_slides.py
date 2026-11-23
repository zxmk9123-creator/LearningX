"""Page snapshots for live classrooms. Keep page assets separate from navigation state."""
import asyncio
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import Lock
from types import SimpleNamespace
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .private_store import MAX_FILES, MAX_TOTAL_BYTES, PDF_UPLOAD_LIMIT, PPTX_UPLOAD_LIMIT, has_material_artifact, read_material_artifact
from .storage import uid
from .storage import Base


class LiveSlide(Base):
    __tablename__ = "live_slides"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, deferred=True)


class LiveTransition(Base):
    __tablename__ = "live_transitions"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("session_id", "version"),)
    page: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class QuestionSlide(Base):
    __tablename__ = "question_slides"
    question_id: Mapped[str] = mapped_column(ForeignKey("session_questions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)


_revisions = OrderedDict()
_revision_lock = Lock()
_pdf_lock = Lock()
_warm_lock = Lock()
_page_load_locks = tuple(Lock() for _ in range(16))
_page_cache_lock = Lock()
_page_cache = OrderedDict()
_page_cache_bytes = 0
_PAGE_CACHE_MAX_BYTES = 24 * 1024 * 1024
_PAGE_CACHE_MAX_PAGES = 12
_waiters = {}


def revision(session_id):
    with _revision_lock:
        return _revisions.get(session_id, "")


def notify(session_id):
    with _revision_lock:
        for loop, event in _waiters.get(session_id, ()):
            loop.call_soon_threadsafe(event.set)
        _revisions[session_id] = uid()
        _revisions.move_to_end(session_id)
        if len(_revisions) > 1024:
            _revisions.popitem(last=False)


async def wait_for_update(session_id, after):
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    waiter = (loop, event)
    with _revision_lock:
        if _revisions.get(session_id, "") != after:
            return
        _waiters.setdefault(session_id, set()).add(waiter)
    try:
        await asyncio.wait_for(event.wait(), timeout=2.5)
    except TimeoutError:
        pass
    finally:
        with _revision_lock:
            waiters = _waiters[session_id]
            waiters.remove(waiter)
            if not waiters:
                del _waiters[session_id]


def _identity(material):
    return material.id, material.course_id, material.out_dir, Path(material.filename).suffix.lower()


def require_preview(material):
    if Path(material.filename).suffix.lower() == ".pptx" and not has_material_artifact(material, "parsed/live-preview.pdf"):
        raise HTTPException(409, "LIVE_PREVIEW_UNAVAILABLE")


@lru_cache(maxsize=2)
def _source(identity):
    material_id, course_id, out_dir, suffix = identity
    record = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    name = "source.pdf" if suffix == ".pdf" else "parsed/live-preview.pdf"
    # A PDF whose Hangul text map was broken is shown from its repaired copy (same drawing).
    if suffix == ".pdf" and has_material_artifact(record, "parsed/live-text.pdf"):
        name = "parsed/live-text.pdf"
    try:
        return read_material_artifact(record, name, PDF_UPLOAD_LIMIT if suffix == ".pdf" else PPTX_UPLOAD_LIMIT)
    except HTTPException as exc:
        if suffix == ".pptx" and exc.status_code == 404:
            raise HTTPException(409, "LIVE_PREVIEW_UNAVAILABLE") from exc
        raise


def source_pdf(material):
    return _source(_identity(material))


@lru_cache(maxsize=8)
def _pages(identity):
    material_id, course_id, out_dir, _ = identity
    material = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    raw = read_material_artifact(material, "parsed/document.json", 24 * 1024 * 1024)
    pages = json.loads(raw)["pages"]
    return {entry["page"]: bool(entry.get("hidden_slide")) for entry in pages}


def visible_pages(material):
    return sorted(page for page, hidden in _pages(_identity(material)).items() if not hidden)


def build_snapshot(material, page):
    pages = _pages(_identity(material))
    if page not in pages or pages[page]:
        raise HTTPException(422, "Slide does not exist or is hidden")
    # Only metadata is saved on navigation. The browser requests page bytes
    # separately and retains pages it has already rendered.
    return max(pages), {"pdf": True, "before": max((n for n in pages if n < page and not pages[n]), default=None),
                        "after": min((n for n in pages if n > page and not pages[n]), default=None)}


def _page_artifact(page):
    return f"parsed/live-pages/{page}.pdf"


def _split_pdf(original, page):
    import pymupdf

    with pymupdf.open() as result:
        result.insert_pdf(original, from_page=page - 1, to_page=page - 1,
                          links=False, annots=False, widgets=False)
        return result.tobytes(garbage=4, deflate=True)


def prepare_live_pages(root, source, pages):
    """Prepare a bounded subset; uncached pages remain available on demand."""
    import pymupdf

    existing = [path for path in root.rglob("*") if path.is_file()]
    remaining_files = min(48, MAX_FILES - len(existing) - 1)  # reserve a signed manifest entry
    remaining_bytes = min(24 * 1024 * 1024, MAX_TOTAL_BYTES - sum(p.stat().st_size for p in existing))
    if remaining_files <= 0 or remaining_bytes <= 0:
        return 0
    count = 0
    with pymupdf.open(source) as original:
        for entry in pages:
            page = entry["page"]
            if entry.get("hidden_slide") or not 1 <= page <= len(original):
                continue
            data = _split_pdf(original, page)
            if len(data) > remaining_bytes:
                break
            target = root / _page_artifact(page)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            count += 1
            remaining_bytes -= len(data)
            if count >= remaining_files or remaining_bytes <= 0:
                break
    return count


def _cached_page(key):
    with _page_cache_lock:
        content = _page_cache.get(key)
        if content is not None:
            _page_cache.move_to_end(key)
        return content


def _page_pdf(identity, page):
    global _page_cache_bytes
    key = (identity, page)
    content = _cached_page(key)
    if content is not None:
        return content

    # Requests for the same page share one storage read or split.
    with _page_load_locks[hash(key) % len(_page_load_locks)]:
        content = _cached_page(key)
        if content is not None:
            return content
        content = _load_page(identity, page)
        if len(content) <= _PAGE_CACHE_MAX_BYTES:
            with _page_cache_lock:
                while (_page_cache_bytes + len(content) > _PAGE_CACHE_MAX_BYTES
                       or len(_page_cache) >= _PAGE_CACHE_MAX_PAGES):
                    _, evicted = _page_cache.popitem(last=False)
                    _page_cache_bytes -= len(evicted)
                _page_cache[key] = content
                _page_cache_bytes += len(content)
        return content


def _load_page(identity, page):
    import pymupdf

    material_id, course_id, out_dir, _ = identity
    record = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    try:
        return read_material_artifact(record, _page_artifact(page), 24 * 1024 * 1024)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    source = _source(identity)
    with _pdf_lock, pymupdf.open(stream=source, filetype="pdf") as original:
        if not 1 <= page <= len(original):
            raise HTTPException(404, "PDF page unavailable")
        return _split_pdf(original, page)


def page_pdf(material, page):
    return _page_pdf(_identity(material), page)


def warm_live_pages(material, pages):
    """Prepare the opening pages after the teacher response, without DB access."""
    if not pages or not _warm_lock.acquire(blocking=False):
        return
    try:
        for page in pages[:8]:
            try:
                page_pdf(material, page)
            except Exception:
                # The normal authorized request still reports the storage error.
                return
    finally:
        _warm_lock.release()


_prepare_pool = ThreadPoolExecutor(4, thread_name_prefix="live-page")


def prepare_shared_page(material, page, before, after):
    """Load the published page alongside the database write; warm its neighbours after."""
    def load():
        try:
            page_pdf(material, page)
        except Exception:
            pass  # The student's page request reports the storage error.
        for neighbour in (after, before):
            if neighbour is not None:
                _prepare_pool.submit(lambda n=neighbour: _quiet(page_pdf, material, n))
    return _prepare_pool.submit(load)


def _quiet(function, *args):
    try:
        function(*args)
    except Exception:
        pass


def cached_live_page(material, page):
    """Return a prepared page without storage I/O or PDF splitting."""
    if material is None:
        return None
    return _cached_page((_identity(material), page))
