"""Read and save the logged-in trader's voyage calculator defaults.

The trader is identified by Chainlit's login cookie, which the browser sends to
these routes too because the chat is mounted on the same site.
"""

from __future__ import annotations

from typing import Any

import jwt
from chainlit.auth.cookie import get_token_from_cookies
from chainlit.auth.jwt import decode_jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ai_platform.backend.voyage import DEFAULT_FIELDS, VoyageInputs
from ai_platform.backend.voyage_defaults import (
    load_voyage_defaults,
    save_voyage_defaults,
)

router = APIRouter(prefix="/api/voyage-defaults", tags=["voyage-defaults"])


def _user_identifier(request: Request) -> str:
    """Read the logged-in trader from the Chainlit login cookie.

    Parameters
    ----------
    request : Request
        Incoming request.

    Returns
    -------
    str
        The trader's login identifier.

    Raises
    ------
    HTTPException
        401 when there is no valid login.
    """
    token = get_token_from_cookies(request.cookies)
    try:
        user = decode_jwt(token) if token else None
    except jwt.PyJWTError:
        user = None
    if user is None:
        raise HTTPException(status_code=401, detail="Log in to the chat first.")
    return user.identifier


@router.get("")
async def read_defaults(request: Request) -> dict[str, Any]:
    """Return the editable fields and the trader's saved values.

    Parameters
    ----------
    request : Request
        Incoming request, carrying the login cookie.

    Returns
    -------
    dict
        ``user``, ``fields`` (name, label, unit, group, calculator default) and
        ``saved`` (input name to saved value).
    """
    user = _user_identifier(request)
    return {"user": user, "fields": DEFAULT_FIELDS, "saved": await load_voyage_defaults(user)}


@router.post("")
async def write_defaults(request: Request, input_values: dict[str, float]) -> dict[str, Any]:
    """Save the trader's defaults after checking them against the calculator.

    Parameters
    ----------
    request : Request
        Incoming request, carrying the login cookie.
    input_values : dict of str to float
        Input name to value; only fields in ``DEFAULT_FIELDS`` are kept.

    Returns
    -------
    dict
        ``saved``: what was stored.

    Raises
    ------
    HTTPException
        422 when a value is out of range for the calculator.
    """
    user = _user_identifier(request)
    allowed = {field["name"] for field in DEFAULT_FIELDS}
    kept = {name: value for name, value in input_values.items() if name in allowed}
    try:
        VoyageInputs(**kept)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=[{"msg": f"{detail['loc'][0]}: {detail['msg']}"} for detail in error.errors()]) from error
    await save_voyage_defaults(user, kept)
    return {"saved": kept}
