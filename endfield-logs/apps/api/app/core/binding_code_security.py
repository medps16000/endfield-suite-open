from collections import OrderedDict, deque
import logging
import math
from threading import Lock
from time import monotonic

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


GENERATE_PATH = "/api/auth/binding-code"
LOOKUP_PATH = "/api/battles/users/binding-code"


class LookupRateLimiter:
    def __init__(self, limit: int = 10, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._buckets: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = Lock()

    def retry_after(self, key: str) -> int:
        now = monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            # Buckets are ordered by the most recent accepted request.
            while self._buckets:
                oldest = next(iter(self._buckets.values()))
                if oldest[-1] > cutoff:
                    break
                self._buckets.popitem(last=False)
            if key not in self._buckets:
                if len(self._buckets) >= 10000:
                    self._buckets.popitem(last=False)
                self._buckets[key] = deque()
            bucket = self._buckets[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return max(1, math.ceil(bucket[0] + self.window_seconds - now))
            bucket.append(now)
            self._buckets.move_to_end(key)
            return 0


class BindingCodeProtectionMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        self.lookup_limiter = LookupRateLimiter()

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/")
        if path not in {GENERATE_PATH, LOOKUP_PATH}:
            return await call_next(request)
        # request.client is resolved by Uvicorn's trusted-proxy handling.
        # Never trust an arbitrary X-Forwarded-For header here.
        retry = self.lookup_limiter.retry_after(request.client.host if request.client else "unknown") \
            if path == LOOKUP_PATH and request.method == "GET" else 0
        if retry:
            response = JSONResponse(
                status_code=429,
                content={"error": {"code": "too_many_requests", "message": "查询过于频繁，请稍后再试。"}},
                headers={"Retry-After": str(retry)},
            )
        else:
            response = await call_next(request)
        # Covers 401/403/404/422/429 as well as successful responses.
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


class BindingCodeAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            target = str(args[2])
            if target.split("?", 1)[0].rstrip("/") == LOOKUP_PATH:
                args[2] = LOOKUP_PATH + "?<redacted>"
                record.args = tuple(args)
        return True


def install_binding_code_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, BindingCodeAccessLogFilter) for item in logger.filters):
        logger.addFilter(BindingCodeAccessLogFilter())
