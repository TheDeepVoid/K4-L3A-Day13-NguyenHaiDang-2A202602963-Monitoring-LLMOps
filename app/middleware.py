from __future__ import annotations

import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

CORRELATION_ID_HEADER = "x-request-id"
RESPONSE_TIME_HEADER = "x-response-time-ms"

# A client-supplied correlation id is untrusted input that ends up in a log file
# and in response headers, so only accept a conservative character set.
INBOUND_CORRELATION_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def resolve_correlation_id(inbound: str | None) -> str:
    """Reuse a caller-supplied id when it is safe, otherwise mint a new one."""
    candidate = (inbound or "").strip()
    if candidate and INBOUND_CORRELATION_ID.match(candidate):
        return candidate
    return f"req-{uuid.uuid4().hex[:8]}"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Structlog contextvars live in a context-local dict. Without this reset a
        # request that never binds a key (or one that binds fewer keys) would
        # inherit the previous request's context and leak it into the log file.
        clear_contextvars()

        correlation_id = resolve_correlation_id(
            request.headers.get(CORRELATION_ID_HEADER)
        )
        bind_contextvars(correlation_id=correlation_id)
        request.state.correlation_id = correlation_id

        start = time.perf_counter()
        response = await call_next(request)
        response_ms = (time.perf_counter() - start) * 1000

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[RESPONSE_TIME_HEADER] = f"{response_ms:.2f}"
        return response
