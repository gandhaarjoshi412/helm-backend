from __future__ import annotations
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from apps.api.app.config import settings
from apps.api.app.security.rate_limiter import general_limiter


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Appends standard security hardening headers to all HTTP responses.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Check payload size
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > settings.MAX_REQUEST_BODY_BYTES:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={
                            "error": {
                                "code": "PAYLOAD_TOO_LARGE",
                                "message": f"Request body exceeds maximum allowed limit of {settings.MAX_REQUEST_BODY_BYTES // (1024 * 1024)}MB.",
                            }
                        },
                    )
            except ValueError:
                pass

        # Apply global rate limit (skip health check)
        if settings.RATE_LIMIT_ENABLED and not request.url.path.startswith("/health"):
            try:
                await general_limiter.check(request, custom_rpm=settings.RATE_LIMIT_DEFAULT_RPM)
            except Exception as e:
                if hasattr(e, "status_code") and e.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"error": {"code": "RATE_LIMIT_EXCEEDED", "message": e.detail}},
                        headers=getattr(e, "headers", {}),
                    )
                raise e

        response: Response = await call_next(request)

        # Append hardening headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        return response
