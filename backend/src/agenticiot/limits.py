"""Bound request bytes before JSON parsing, without retaining bodies beyond the request."""

import asyncio

from starlette.responses import JSONResponse

BODY_TIMEOUT = 5


class RequestLimits:
    def __init__(self, app):
        self.app = app
        self.active = {"http": 0, "events": 0, "nodes": 0}
        self.capacity = {"http": 64, "events": 32, "nodes": 64}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"} or scope.get("path") == "/health/live":
            return await self.app(scope, receive, send)
        bucket = (
            "nodes"
            if scope["type"] == "websocket"
            else "events"
            if scope["path"] == "/v1/events"
            else "http"
        )
        if self.active[bucket] >= self.capacity[bucket]:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
                return
            return await JSONResponse(
                {"code": "platform_busy"}, status_code=503, headers={"Retry-After": "2"}
            )(scope, receive, send)
        self.active[bucket] += 1
        try:
            await self.bounded(scope, receive, send)
        finally:
            self.active[bucket] -= 1

    async def bounded(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = 131072 if scope["path"] == "/v1/chat/completions" else 4 * 1024 * 1024
        chunks, total = [], 0
        deadline = asyncio.get_running_loop().time() + BODY_TIMEOUT
        while True:
            try:
                async with asyncio.timeout_at(deadline):
                    message = await receive()
            except TimeoutError:
                return await JSONResponse({"code": "request_timeout"}, status_code=408)(
                    scope, receive, send
                )
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > limit:
                response = JSONResponse(
                    {"code": "request_too_large", "status": 413}, status_code=413
                )
                return await response(scope, receive, send)
            chunks.append(body)
            if not message.get("more_body", False):
                break
        content = b"".join(chunks)
        chunks.clear()
        delivered = False

        async def bounded_receive():
            nonlocal delivered, content
            if delivered:
                return await receive()
            delivered = True
            result = {"type": "http.request", "body": content, "more_body": False}
            content = b""
            return result

        await self.app(scope, bounded_receive, send)
