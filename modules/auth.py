"""Lightweight session-based authentication.

Why not Flask-Login?
    The runtime environment can't install it (offline). This module
    uses Flask's built-in cookie session + bcrypt, which is enough

What it provides
----------------
* :class:`UserStore`       — file-backed user DB (``data/users.json``)
* :func:`hash_password`    — bcrypt hashing
* :func:`verify_password`  — constant-time check
* :func:`current_user`     — read the logged-in user from ``flask.session``
* :func:`login_user`       — populate the session
* :func:`logout_user`      — clear the session
* :func:`login_required`   — decorator for protected routes
* :func:`role_required`    — decorator for role-gated routes

Schema of ``data/users.json``
-----------------------------
::

    {
        "version": 1,
        "users": [
            {
                "username": "admin",
                "password_hash": "$2b$12$...",
                "role": "admin",
                "created_at": "2026-06-02T10:00:00",
                "last_login_at": "2026-06-02T14:30:00"
            },
            ...
        ]
    }

Default admin
-------------
If the file is missing, :class:`UserStore.ensure_default_admin`
creates ``admin`` with password ``admin`` (forced to be changed on
first login in v20).  Local dev only — production should set
``JZTZ_BOOTSTRAP_ADMIN_PASSWORD`` or pre-populate the file.
"""
from __future__ import annotations

import functools
import warnings
import json
import os
import re
import secrets
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

import bcrypt

if TYPE_CHECKING:
    from flask import Flask

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_HERE, "data")
DEFAULT_FILE = os.path.join(DATA_DIR, "users.json")
SCHEMA_VERSION = 1
SESSION_KEY = "auth_user_id"
SESSION_ROLE = "auth_user_role"
CSRF_KEY = "auth_csrf"

MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 64
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128
BCRYPT_ROUNDS = 12

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = os.environ.get(
    "JZTZ_BOOTSTRAP_ADMIN_PASSWORD", "admin123"
)
if DEFAULT_ADMIN_PASSWORD == "admin123":
    warnings.warn(
        "JZTZ_BOOTSTRAP_ADMIN_PASSWORD not set; using default password 'admin123'. "
        "Set JZTZ_BOOTSTRAP_ADMIN_PASSWORD env var in production."
    )

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
class AuthError(Exception):
    """Base auth error."""


class UserNotFound(AuthError):
    """No user with that username."""


class DuplicateUser(AuthError):
    """A user with that username already exists."""


class InvalidCredentials(AuthError):
    """Bad username/password combination."""


class ValidationError(AuthError):
    """Invalid input field."""


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(plain: str) -> str:
    """Return a bcrypt hash of ``plain``. Includes the salt + cost as a string."""
    if not isinstance(plain, str):
        raise ValidationError("password must be a string")
    if not (MIN_PASSWORD_LEN <= len(plain) <= MAX_PASSWORD_LEN):
        raise ValidationError(
            f"password length must be {MIN_PASSWORD_LEN}-{MAX_PASSWORD_LEN}"
        )
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time compare. Returns False on any decode / format error."""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Username validation
# ---------------------------------------------------------------------------


def _validate_username(username: str) -> str:
    if not isinstance(username, str):
        raise ValidationError("username must be a string")
    username = username.strip()
    if not USERNAME_RE.match(username):
        raise ValidationError(
            "username must be 3-64 chars, A-Za-z0-9_.- only"
        )
    return username


# ---------------------------------------------------------------------------
# File-backed user store
# ---------------------------------------------------------------------------
class UserStore:
    """Thread-safe JSON store of users — 带内存缓存，避免每次请求读文件"""

    def __init__(self, path: str = DEFAULT_FILE) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_mtime: float = 0.0  # 文件修改时间，用于检测外部变更
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            self._write_atomic(self._empty())

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {"version": SCHEMA_VERSION, "users": []}

    def _read(self) -> Dict[str, Any]:
        """读取用户数据 — 优先使用内存缓存，文件未变时不读磁盘"""
        # 检查文件修改时间，如果没变就直接用缓存
        try:
            mtime = os.path.getmtime(self.path)
        except OSError:
            mtime = 0.0

        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache

        # 文件有变化或首次读取，从磁盘加载
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = self._empty()
        except (json.JSONDecodeError, OSError):
            data = self._empty()
        if "users" not in data or not isinstance(data["users"], list):
            data = self._empty()

        self._cache = data
        self._cache_mtime = mtime
        return data

    def _invalidate_cache(self) -> None:
        """写操作后使缓存失效"""
        self._cache = None
        self._cache_mtime = 0.0

    def _write_atomic(self, data: Dict[str, Any]) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)
        self._invalidate_cache()

    # ---- queries --------------------------------------------------------
    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [self._public_view(u) for u in self._read()["users"]]

    def get(self, username: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            for u in self._read()["users"]:
                if u.get("username") == username:
                    return dict(u)
        return None

    def exists(self, username: str) -> bool:
        return self.get(username) is not None

    def has_admin(self) -> bool:
        with self._lock:
            return any(
                u.get("role") == "admin" for u in self._read()["users"]
            )

    # ---- mutations ------------------------------------------------------
    def create(
        self,
        username: str,
        password: str,
        role: str = "user",
    ) -> Dict[str, Any]:
        username = _validate_username(username)
        if role not in {"user", "admin"}:
            raise ValidationError(f"unknown role {role!r}")
        password_hash = hash_password(password)
        with self._lock:
            data = self._read()
            for existing in data["users"]:
                if existing.get("username") == username:
                    raise DuplicateUser(f"user {username!r} already exists")
            user = {
                "username": username,
                "password_hash": password_hash,
                "role": role,
                "created_at": datetime.utcnow().isoformat(timespec="seconds"),
                "last_login_at": None,
            }
            data["users"].append(user)
            self._write_atomic(data)
        return self._public_view(user)

    def delete(self, username: str) -> bool:
        with self._lock:
            data = self._read()
            before = len(data["users"])
            data["users"] = [
                u for u in data["users"] if u.get("username") != username
            ]
            if len(data["users"]) == before:
                return False
            self._write_atomic(data)
        return True

    def change_password(
        self, username: str, new_password: str
    ) -> Dict[str, Any]:
        new_hash = hash_password(new_password)
        with self._lock:
            data = self._read()
            for u in data["users"]:
                if u.get("username") == username:
                    u["password_hash"] = new_hash
                    self._write_atomic(data)
                    return self._public_view(u)
        raise UserNotFound(username)

    def record_login(self, username: str) -> None:
        with self._lock:
            data = self._read()
            for u in data["users"]:
                if u.get("username") == username:
                    u["last_login_at"] = datetime.utcnow().isoformat(
                        timespec="seconds"
                    )
                    self._write_atomic(data)
                    return

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        # Always run bcrypt (even when the user doesn't exist) so the
        # response time doesn't leak which usernames are registered.
        # The dummy hash is the cost-equivalent of a real one and is
        # guaranteed never to match a caller-supplied password.
        _DUMMY_HASH = (
            "$2b$12$CwTycUXWue0Thq9StjUM0uJ8hZ4H7u8xK2vN1pQ3rS5tU7vW9yZ1aC"
        )
        user = self.get(username)
        candidate_hash = (
            user.get("password_hash", "") if user is not None else _DUMMY_HASH
        )
        if not verify_password(password, candidate_hash) or user is None:
            raise InvalidCredentials("invalid username or password")
        self.record_login(username)
        return self._public_view(user)

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def _public_view(user: Dict[str, Any]) -> Dict[str, Any]:
        """Strip the password hash before returning the user record."""
        return {
            "username": user.get("username"),
            "role": user.get("role", "user"),
            "created_at": user.get("created_at"),
            "last_login_at": user.get("last_login_at"),
        }

    def ensure_default_admin(self) -> Optional[Dict[str, Any]]:
        """Create the bootstrap admin user if no admin exists yet."""
        if self.has_admin():
            return None
        return self.create(
            DEFAULT_ADMIN_USERNAME,
            DEFAULT_ADMIN_PASSWORD,
            role="admin",
        )


# ---------------------------------------------------------------------------
# Module-level default store
# ---------------------------------------------------------------------------
_default_lock = threading.Lock()
_default: Optional[UserStore] = None


def get_default_store() -> UserStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = UserStore()
            _default.ensure_default_admin()
    return _default


def reset_default_store() -> None:
    global _default
    with _default_lock:
        _default = None


# ---------------------------------------------------------------------------
# Username regex (also imported by routes/auth.py)
# ---------------------------------------------------------------------------
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


# ---------------------------------------------------------------------------
# Session helpers (Flask)
# ---------------------------------------------------------------------------
def login_user(user: Dict[str, Any], flask_app: Optional["Flask"] = None) -> None:
    """Store the user id in the Flask session and rotate the CSRF token."""
    from flask import session
    session.permanent = True
    session[SESSION_KEY] = user["username"]
    session[SESSION_ROLE] = user.get("role", "user")
    session[CSRF_KEY] = secrets.token_urlsafe(24)


def logout_user() -> None:
    from flask import session
    session.pop(SESSION_KEY, None)
    session.pop(SESSION_ROLE, None)
    session.pop(CSRF_KEY, None)


def current_user() -> Optional[Dict[str, Any]]:
    """Return the logged-in user dict, or None."""
    from flask import session
    username = session.get(SESSION_KEY)
    if not username:
        return None
    store = get_default_store()
    user = store.get(username)
    if user is None:
        # User was deleted out from under the session.
        logout_user()
        return None
    return store._public_view(user)


def current_csrf_token() -> Optional[str]:
    from flask import session
    return session.get(CSRF_KEY)


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------
def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Reject anonymous calls with 401 JSON."""
    import inspect
    from flask import jsonify

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if current_user() is None:
            return jsonify({
                "success": False,
                "error": "authentication required",
            }), 401
        return view(*args, **kwargs)

    # Preserve async detection for the existing route helpers
    wrapper._is_async = getattr(view, "_is_async", False)  # type: ignore[attr-defined]
    wrapper.__wrapped__ = view  # type: ignore[attr-defined]
    if inspect.iscoroutinefunction(view):
        wrapper._is_async = True  # type: ignore[attr-defined]
    return wrapper


def role_required(*roles: str) -> Callable[..., Any]:
    """Reject callers whose role is not in ``roles``."""
    allowed = set(roles)
    from flask import jsonify

    def decorator(view: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(view)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            user = current_user()
            if user is None:
                return jsonify({
                    "success": False,
                    "error": "authentication required",
                }), 401
            if user.get("role") not in allowed:
                return jsonify({
                    "success": False,
                    "error": f"role required: {sorted(allowed)}",
                }), 403
            return view(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# CSRF (very small — one token per session, sent in X-CSRF-Token header)
# ---------------------------------------------------------------------------
def register_csrf_protection(app: "Flask") -> None:
    """Install a ``before_request`` hook that enforces CSRF on unsafe verbs.

    Exempt routes (login/logout/whoami) are matched by prefix so the
    user can establish a session before having a token to send back.
    """
    from flask import jsonify, request, session

    @app.before_request
    def _csrf_hook() -> Any:
        if request.method in {"GET", "HEAD", "OPTIONS"}:
            return None
        if request.path.startswith(("/api/auth/login", "/api/auth/logout", "/api/auth/whoami")):
            return None
        sent = request.headers.get("X-CSRF-Token", "")
        expected = session.get(CSRF_KEY)
        if not expected or not sent or sent != expected:
            return jsonify({
                "success": False,
                "error": "CSRF token missing or invalid",
            }), 403
        return None


__all__ = [
    "AuthError",
    "UserNotFound",
    "DuplicateUser",
    "InvalidCredentials",
    "ValidationError",
    "UserStore",
    "USERNAME_RE",
    "hash_password",
    "verify_password",
    "get_default_store",
    "reset_default_store",
    "login_user",
    "logout_user",
    "current_user",
    "current_csrf_token",
    "login_required",
    "role_required",
    "register_csrf_protection",
    "SESSION_KEY",
    "SESSION_ROLE",
    "CSRF_KEY",
]
