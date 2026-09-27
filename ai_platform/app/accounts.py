"""Individual trader accounts. CLI passwords are read privately, never arguments.

Provision: python -m ai_platform.app.accounts add <identifier>
Reset:     python -m ai_platform.app.accounts reset <identifier>
Disable:   python -m ai_platform.app.accounts disable <identifier>
Enable:    python -m ai_platform.app.accounts enable <identifier>
Then set OI_AUTH_MODE=accounts. OI_ENVIRONMENT=production forbids development login.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
import re
import secrets
from getpass import getpass

from fastapi import HTTPException

from ai_platform.backend.db import _get_pool, close_pool

ITERATIONS = 600_000


def auth_mode():
    mode = os.environ.get("OI_AUTH_MODE", "development")
    if mode not in ("development", "accounts"):
        raise RuntimeError("OI_AUTH_MODE must be development or accounts")
    if os.environ.get("OI_ENVIRONMENT") == "production" and mode != "accounts":
        raise RuntimeError("Production requires OI_AUTH_MODE=accounts")
    return mode


def hash_password(password):
    if not 12 <= len(password) <= 1024:
        raise ValueError("Use a password between 12 and 1024 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest}"


def verify_password(password, stored):
    if len(password) > 1024:
        return False
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if (
            algorithm != "pbkdf2_sha256"
            or not ITERATIONS <= int(iterations) <= 2_000_000
        ):
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


# Same work for unknown accounts, without storing a usable default password.
DUMMY_HASH = f"pbkdf2_sha256${ITERATIONS}${'00' * 16}${'00' * 32}"


async def authenticate(identifier, password):
    mode = auth_mode()
    if mode == "development":
        return (
            {"role": "dev", "auth_mode": mode}
            if identifier == "dev" and hmac.compare_digest(password.encode(), b"dev")
            else None
        )
    if len(identifier) > 120 or len(password) > 1024:
        return None
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection, connection.transaction():
        await connection.execute("SELECT pg_advisory_xact_lock(483,2)")
        actor = hashlib.sha256(identifier.encode()).hexdigest()
        counts = await connection.fetchrow(
            """SELECT
          count(*) FILTER (WHERE actor_hash=$1) AS account_count,
          count(*) FILTER (WHERE created_at > now()-interval '1 minute') AS global_count
          FROM public.oi_login_attempts WHERE created_at > now()-interval '15 minutes'""",
            actor,
        )
        if counts["account_count"] >= 10 or counts["global_count"] >= 120:
            return None
        await connection.execute(
            "INSERT INTO public.oi_login_attempts(actor_hash) VALUES ($1)", actor
        )
        row = await connection.fetchrow(
            "SELECT password_hash, active, session_version FROM public.oi_trader_accounts WHERE identifier=$1",
            identifier,
        )
    valid = await asyncio.to_thread(
        verify_password, password, row["password_hash"] if row else DUMMY_HASH
    )
    if not row or not row["active"] or not valid:
        return None
    return {
        "role": "trader",
        "auth_mode": "accounts",
        "session_version": row["session_version"],
    }


async def verify_session(identifier, metadata):
    if auth_mode() != "accounts":
        return
    if metadata.get("auth_mode") != "accounts":
        raise HTTPException(401, "Sign in with your individual trader account.")
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection:
        row = await connection.fetchrow(
            "SELECT active, session_version FROM public.oi_trader_accounts WHERE identifier=$1",
            identifier,
        )
    if (
        not row
        or not row["active"]
        or row["session_version"] != metadata.get("session_version")
    ):
        raise HTTPException(401, "This session was revoked. Sign in again.")


async def manage(action, identifier, password_hash=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._+-]{0,119}", identifier):
        raise ValueError(
            "Use a stable username or email address, at most 120 characters"
        )
    pool = await _get_pool()
    async with pool.acquire(timeout=10) as connection:
        if action == "add":
            await connection.execute(
                "INSERT INTO public.oi_trader_accounts(identifier,password_hash) VALUES ($1,$2)",
                identifier,
                password_hash,
            )
            return
        if action == "reset":
            result = await connection.execute(
                "UPDATE public.oi_trader_accounts SET password_hash=$2,session_version=session_version+1,updated_at=now() WHERE identifier=$1",
                identifier,
                password_hash,
            )
        else:
            result = await connection.execute(
                "UPDATE public.oi_trader_accounts SET active=$2,session_version=session_version+1,updated_at=now() WHERE identifier=$1",
                identifier,
                action == "enable",
            )
    if result == "UPDATE 0":
        raise ValueError("Account not found")


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["add", "reset", "disable", "enable"])
    parser.add_argument("identifier")
    args = parser.parse_args()
    password_hash = None
    if args.action in ("add", "reset"):
        password = getpass("Password (at least 12 characters): ")
        if password != getpass("Confirm password: "):
            raise ValueError("Passwords do not match")
        password_hash = hash_password(password)
    try:
        await manage(args.action, args.identifier, password_hash)
        print("Account updated. Credential changes revoke existing desk sessions.")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())
