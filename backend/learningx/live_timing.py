"""Request-local timings (Server-Timing) for slide delivery and student question endpoints.

The question endpoints also report how many database queries they made and how long
those took, so a slow class can be told apart: database round trips (for example an API
host far from the database region) versus model calls.
"""
from contextvars import ContextVar
from time import perf_counter

from fastapi.routing import APIRoute
from sqlalchemy import event

from .storage import engine

_DB = ContextVar("learningx_db_timing", default=None)
TIMED_SUFFIXES = ("/live", "/live/page/{version}", "/questions", "/revisions")


@event.listens_for(engine, "before_cursor_execute")
def _query_started(conn, cursor, statement, parameters, context, executemany):
    if _DB.get() is not None:
        context._learningx_started = perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _query_done(conn, cursor, statement, parameters, context, executemany):
    acc = _DB.get()
    started = getattr(context, "_learningx_started", None)
    if acc is not None and started is not None:
        acc[0] += 1
        acc[1] += perf_counter() - started


class LiveTimingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not self.path.endswith(TIMED_SUFFIXES):
            return handler

        async def timed(request):
            started = perf_counter()
            request.state.live_timing = {}
            acc = [0, 0.0]
            token = _DB.set(acc)  # sync endpoints run with a copy of this context: same list
            try:
                response = await handler(request)
            finally:
                _DB.reset(token)
            phases = request.state.live_timing
            phases["app"] = (perf_counter() - started) * 1000
            parts = [f"{name};dur={duration:.2f}" for name, duration in phases.items()]
            if acc[0]:
                parts.append(f'db;dur={acc[1] * 1000:.2f};desc="{acc[0]} queries"')
            response.headers["Server-Timing"] = ", ".join(parts)
            response.headers["Timing-Allow-Origin"] = "*"
            return response

        return timed


def record_phase(request, name, started):
    timings = request.state.live_timing
    timings[name] = timings.get(name, 0) + (perf_counter() - started) * 1000
