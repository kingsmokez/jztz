"""HTTP routes for authentication.

Endpoints
---------
* ``POST /api/auth/login``    — username + password → session cookie
* ``POST /api/auth/logout``   — clear session
* ``GET  /api/auth/whoami``   — current user (or 401)
* ``POST /api/auth/register`` — create a new user (admin-only)
* ``POST /api/auth/password`` — change own password
* ``GET  /api/auth/users``    — list users (admin-only)
* ``DELETE /api/auth/users/<username>`` — remove a user (admin-only)

CSRF
----
Mutating routes (everything except login / whoami) require an
``X-CSRF-Token`` header that matches the session token. The token
is rotated on every successful login and exposed via
``GET /api/auth/whoami`` for clients to read.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

from modules.auth import (
    DuplicateUser,
    InvalidCredentials,
    UserNotFound,
    USERNAME_RE,
    UserStore,
    ValidationError,
    current_csrf_token,
    current_user,
    get_default_store,
    login_user,
    logout_user,
    role_required,
)
from modules.logger import log


auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _store() -> UserStore:
    return get_default_store()


def _err(exc: Exception) -> Any:
    if isinstance(exc, ValidationError):
        return jsonify({"success": False, "error": str(exc)}), 400
    if isinstance(exc, InvalidCredentials):
        return jsonify({"success": False, "error": str(exc)}), 401
    if isinstance(exc, DuplicateUser):
        return jsonify({"success": False, "error": str(exc)}), 409
    if isinstance(exc, UserNotFound):
        return jsonify({"success": False, "error": str(exc)}), 404
    log.error(f"auth error: {exc}", exc_info=True)
    return jsonify({"success": False, "error": f"internal: {exc}"}), 500


def _parse_json() -> Dict[str, Any]:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValidationError("body must be a JSON object")
    return data


# ---------------------------------------------------------------------------
# Login / logout / whoami
# ---------------------------------------------------------------------------
@auth_bp.post("/login")
def login():
    """Body: ``{"username": "...", "password": "..."}``.

    On success: sets a session cookie + returns the user record +
    CSRF token (for the client to echo back on subsequent mutations).
    """
    try:
        data = _parse_json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username or not password:
            raise ValidationError("username and password are required")
        user = _store().authenticate(username, password)
        login_user(user)
        return jsonify({
            "success": True,
            "user": user,
            "csrf_token": current_csrf_token(),
        })
    except Exception as e:
        return _err(e)


@auth_bp.post("/logout")
def logout():
    logout_user()
    return jsonify({"success": True})


@auth_bp.get("/whoami")
def whoami():
    user = current_user()
    if user is None:
        return jsonify({"success": False, "error": "not logged in"}), 401
    return jsonify({
        "success": True,
        "user": user,
        "csrf_token": current_csrf_token(),
    })


# ---------------------------------------------------------------------------
# Self-service
# ---------------------------------------------------------------------------
@auth_bp.post("/password")
def change_password():
    """Change the caller's password. Body: ``{old_password, new_password}``."""
    try:
        data = _parse_json()
        user = current_user()
        if user is None:
            return jsonify({
                "success": False, "error": "authentication required"
            }), 401
        old = data.get("old_password") or ""
        new = data.get("new_password") or ""
        if not old or not new:
            raise ValidationError("old_password and new_password are required")
        # Verify old password
        full = _store().get(user["username"])
        if full is None:
            raise UserNotFound(user["username"])
        from modules.auth import verify_password
        if not verify_password(old, full.get("password_hash", "")):
            raise InvalidCredentials("old password is incorrect")
        updated = _store().change_password(user["username"], new)
        return jsonify({"success": True, "user": updated})
    except Exception as e:
        return _err(e)


# ---------------------------------------------------------------------------
# Admin: user CRUD
# ---------------------------------------------------------------------------
@auth_bp.get("/users")
@role_required("admin")
def list_users():
    return jsonify({
        "success": True,
        "count": len(_store().list()),
        "users": _store().list(),
    })


@auth_bp.post("/register")
@role_required("admin")
def register_user():
    """Body: ``{username, password, role?}``. Role defaults to ``user``."""
    try:
        data = _parse_json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        role = (data.get("role") or "user").strip()
        if not username or not password:
            raise ValidationError("username and password are required")
        user = _store().create(username, password, role=role)
        return jsonify({"success": True, "user": user}), 201
    except Exception as e:
        return _err(e)


@auth_bp.delete("/users/<username>")
@role_required("admin")
def delete_user(username: str):
    if not USERNAME_RE.match(username):
        return jsonify({"success": False, "error": "invalid username"}), 400
    me = current_user()
    if me and me["username"] == username:
        return jsonify({
            "success": False,
            "error": "cannot delete the logged-in user",
        }), 400
    ok = _store().delete(username)
    if not ok:
        return jsonify({"success": False, "error": "not found"}), 404
    return jsonify({"success": True, "deleted": username})
