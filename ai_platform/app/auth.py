"""Share Chainlit's signed login session with desk APIs."""

from urllib.parse import urlsplit

from chainlit.auth import get_token_from_cookies
from chainlit.auth.jwt import decode_jwt
from fastapi import HTTPException, Request

from ai_platform.app.accounts import verify_session


async def current_trader(request: Request) -> str:
    token = get_token_from_cookies(request.cookies)
    if not token:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            token = authorization[7:]
    if not token:
        raise HTTPException(401, "Sign in through Chat to use the trading desk.")
    try:
        user = decode_jwt(token)
    except Exception as error:
        raise HTTPException(401, "Your session has expired. Sign in again.") from error
    if not user.identifier or not user.identifier.strip():
        raise HTTPException(401, "Session has no trader identity.")
    await verify_session(user.identifier, user.metadata)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # A custom header prevents cross-site HTML form submissions. Same-origin
        # fetch sends this header; no CORS policy permits other origins to do so.
        if request.headers.get("x-oi-request") != "1":
            raise HTTPException(403, "Missing same-origin request header.")
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.url.netloc:
            raise HTTPException(403, "Cross-origin writes are not allowed.")
    return user.identifier
