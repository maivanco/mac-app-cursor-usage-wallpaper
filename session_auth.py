#!/usr/bin/env python3
"""Resolve Cursor dashboard session tokens from local sources."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import subprocess
from pathlib import Path


def _decode_jwt_sub(token: str) -> str:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    data = json.loads(base64.urlsafe_b64decode(payload))
    sub = data.get("sub")
    if not sub:
        raise ValueError("Could not read 'sub' claim from session token.")
    return str(sub)


def _normalize_token(raw: str) -> str:
    token = raw.strip().strip('"').strip("'")
    if not token:
        raise ValueError("Session token is empty.")
    return token


def _from_state_db() -> str | None:
    db_path = Path.home() / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    if not db_path.exists():
        return None

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'"
        ).fetchone()
    finally:
        conn.close()

    return row[0] if row else None


def _from_keychain() -> str | None:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", "cursor-access-token", "-w"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    token = result.stdout.strip()
    return token or None


def resolve_session_token() -> str:
    env_token = os.getenv("CURSOR_SESSION_TOKEN", "").strip()
    if env_token:
        return _normalize_token(env_token)

    for resolver in (_from_state_db, _from_keychain):
        token = resolver()
        if token:
            return _normalize_token(token)

    raise RuntimeError(
        "No Cursor session found. Sign in to the Cursor app, or set CURSOR_SESSION_TOKEN "
        "from browser cookies (WorkosCursorSessionToken)."
    )


def get_bearer_token(token: str | None = None) -> str:
    raw = _normalize_token(token or resolve_session_token())
    if "::" in raw:
        _, jwt = raw.split("::", 1)
        return jwt
    return raw


def build_session_cookie(token: str) -> str:
    token = _normalize_token(token)
    if "::" in token:
        sub, jwt = token.split("::", 1)
    else:
        sub = _decode_jwt_sub(token)
        jwt = token
    return f"WorkosCursorSessionToken={sub}%3A%3A{jwt}"
