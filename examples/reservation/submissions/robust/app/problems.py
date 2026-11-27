"""Error responses as RFC 9457 Problem Details (application/problem+json)."""

from fastapi import Request
from fastapi.responses import JSONResponse

MEDIA_TYPE = "application/problem+json"


class Problem(Exception):
    def __init__(
        self,
        status: int,
        slug: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.slug = slug
        self.title = title
        self.detail = detail
        self.headers = headers or {}

    def body(self, instance: str | None = None) -> dict:
        body = {"type": f"/problems/{self.slug}", "title": self.title, "status": self.status}
        if self.detail:
            body["detail"] = self.detail
        if instance:
            body["instance"] = instance
        return body


def response(problem: Problem, instance: str | None = None) -> JSONResponse:
    return JSONResponse(
        problem.body(instance), status_code=problem.status, media_type=MEDIA_TYPE, headers=problem.headers
    )


async def handle(request: Request, exc: Problem) -> JSONResponse:
    return response(exc, request.url.path)
