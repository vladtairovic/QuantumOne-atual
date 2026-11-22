"""Streaming request envelope limit, including requests without Content-Length."""
from uuid import uuid4
from fastapi.responses import JSONResponse
from .config import get_limits
from .errors import CircuitError


def error_response(request_id, failure, fields=None):
    # Flat fields remain compatible with existing frontend clients.
    return JSONResponse(status_code=failure.status, content={
        "request_id": request_id, "code": failure.code, "message": failure.message,
        "fields": fields or [], "error": failure.payload(),
    }, headers={"X-Request-ID": request_id})


class CircuitBodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/qiskit/optimize" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        limit = get_limits().max_request_bytes
        chunks, size = [], 0
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            data = event.get("body", b"")
            size += len(data)
            if size > limit:
                failure = CircuitError("REQUEST_TOO_LARGE", "Corpo JSON acima do limite permitido.",
                                       {"limit": limit, "stage": "transport"}, 413)
                return await error_response(str(uuid4()), failure)(scope, receive, send)
            chunks.append(data)
            if not event.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
