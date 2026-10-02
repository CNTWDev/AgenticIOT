"""Bound request bytes before JSON parsing, without retaining bodies beyond the request."""

from starlette.responses import JSONResponse


class RequestLimits:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        limit = 131072 if scope["path"] == "/v1/chat/completions" else 4 * 1024 * 1024
        chunks, total = [], 0
        while True:
            message = await receive()
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
