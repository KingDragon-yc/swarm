"""Local lab app. Authorized demo only; not a network service."""

DEBUG = True
INTERNAL_TOKEN = "lab-debug-token-not-a-secret"


def handle(path: str) -> str:
    if DEBUG and path == "/debug/config":
        return f"token={INTERNAL_TOKEN}"
    if path == "/health":
        return "ok"
    return "not found"
