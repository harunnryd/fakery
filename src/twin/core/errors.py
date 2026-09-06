BASE_TYPE_URL = "https://api.fakery.dev/problems"


class TwinError(Exception):
    def __init__(self, slug: str, title: str, status: int, detail: str = "") -> None:
        super().__init__(detail or title)
        self.slug = slug
        self.title = title
        self.status = status
        self.detail = detail

    def type_url(self) -> str:
        return f"{BASE_TYPE_URL}/{self.slug}"

    def to_problem(self, request_id: str) -> dict:
        return {
            "type": self.type_url(),
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "instance": request_id,
        }


_CATALOG: dict[str, tuple[str, int]] = {
    "validation-failed": ("Request failed validation", 422),
    "unauthorized": ("Missing or invalid credentials", 401),
    "forbidden": ("Action not allowed", 403),
    "not-found": ("Resource not found", 404),
    "conflict": ("State conflict; read detail", 409),
    "rate-limited": ("Quota exhausted", 429),
    "upstream-unavailable": ("Provider unavailable; retry later", 503),
    "internal": ("Internal error", 500),
}


def make_error(slug: str, detail: str = "") -> TwinError:
    try:
        title, status = _CATALOG[slug]
    except KeyError:
        raise ValueError(f"unknown error slug: {slug}") from None
    return TwinError(slug=slug, title=title, status=status, detail=detail)
