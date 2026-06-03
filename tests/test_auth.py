"""Tests for modules.auth + routes.auth."""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def tmp_store(monkeypatch):
    tmpdir = tempfile.mkdtemp(prefix="auth_test_")
    path = os.path.join(tmpdir, "users.json")
    from modules import auth as a_mod
    store = a_mod.UserStore(path)
    monkeypatch.setattr(a_mod, "_default", store, raising=False)
    yield store
    if os.path.exists(path):
        os.unlink(path)
    os.rmdir(tmpdir)


@pytest.fixture
def client():
    from web_app import create_app
    return create_app().test_client()


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
class TestPasswordHashing:
    def test_hash_returns_string(self):
        from modules.auth import hash_password
        h = hash_password("correct horse battery staple")
        assert isinstance(h, str)
        assert h.startswith("$2b$") or h.startswith("$2a$")

    def test_verify_round_trip(self):
        from modules.auth import hash_password, verify_password
        h = hash_password("hunter2hunter2")
        assert verify_password("hunter2hunter2", h) is True

    def test_verify_wrong_password(self):
        from modules.auth import hash_password, verify_password
        h = hash_password("hunter2hunter2")
        assert verify_password("hunter3hunter3", h) is False

    def test_short_password_rejected(self):
        from modules.auth import hash_password, ValidationError
        with pytest.raises(ValidationError):
            hash_password("short")

    def test_long_password_rejected(self):
        from modules.auth import hash_password, ValidationError
        with pytest.raises(ValidationError):
            hash_password("a" * 200)

    def test_verify_empty_inputs(self):
        from modules.auth import verify_password
        assert verify_password("", "$2b$12$" + "x" * 53) is False
        assert verify_password("pw", "") is False

    def test_verify_garbage_hash(self):
        from modules.auth import verify_password
        assert verify_password("pw", "not-a-bcrypt-hash") is False


# ---------------------------------------------------------------------------
# User store CRUD
# ---------------------------------------------------------------------------
class TestUserStore:
    def test_create_assigns_timestamps(self, tmp_store):
        u = tmp_store.create("alice", "password1234", role="user")
        assert u["username"] == "alice"
        assert u["role"] == "user"
        assert u["created_at"] is not None
        assert u["last_login_at"] is None
        # The public view must NOT contain the hash
        assert "password_hash" not in u

    def test_duplicate_raises(self, tmp_store):
        from modules.auth import DuplicateUser
        tmp_store.create("bob", "password1234")
        with pytest.raises(DuplicateUser):
            tmp_store.create("bob", "another12345")

    def test_invalid_username(self, tmp_store):
        from modules.auth import ValidationError
        with pytest.raises(ValidationError):
            tmp_store.create("ab", "password1234")  # too short
        with pytest.raises(ValidationError):
            tmp_store.create("has space", "password1234")
        with pytest.raises(ValidationError):
            tmp_store.create("has@symbol", "password1234")

    def test_invalid_role(self, tmp_store):
        from modules.auth import ValidationError
        with pytest.raises(ValidationError):
            tmp_store.create("charlie", "password1234", role="god")

    def test_authenticate_success(self, tmp_store):
        tmp_store.create("dave", "password1234")
        u = tmp_store.authenticate("dave", "password1234")
        assert u["username"] == "dave"
        # last_login_at should now be set
        fresh = tmp_store.get("dave")
        assert fresh["last_login_at"] is not None

    def test_authenticate_wrong_password(self, tmp_store):
        from modules.auth import InvalidCredentials
        tmp_store.create("erin", "password1234")
        with pytest.raises(InvalidCredentials):
            tmp_store.authenticate("erin", "wrongpassword1")

    def test_authenticate_unknown_user(self, tmp_store):
        from modules.auth import InvalidCredentials
        with pytest.raises(InvalidCredentials):
            tmp_store.authenticate("nobody", "password1234")

    def test_authenticate_unknown_user_runs_bcrypt(self, tmp_store):
        """Timing-attack guard: authenticate() must always run bcrypt, even
        for unknown users, so the response time doesn't leak which
        usernames are registered. We assert two observable invariants:
        (a) both unknown-user and wrong-password raise the same exception
            class, and (b) the error messages are identical so the API
            doesn't leak which usernames exist.
        """
        from modules.auth import InvalidCredentials
        # Unknown user
        with pytest.raises(InvalidCredentials) as exc1:
            tmp_store.authenticate(
                "definitely_not_a_user_xyz", "password1234"
            )
        # Known user with wrong password
        tmp_store.create("known_user", "password1234")
        with pytest.raises(InvalidCredentials) as exc2:
            tmp_store.authenticate("known_user", "wrongpassword9")
        # Identical messages — the API must not reveal which usernames
        # are registered. (The bcrypt timing itself is exercised in the
        # implementation via a dummy hash; we can't reliably time it
        # in a unit test, but the message parity catches the common
        # "return early on missing user" refactor.)
        assert str(exc1.value) == str(exc2.value)

    def test_change_password(self, tmp_store):
        from modules.auth import InvalidCredentials
        tmp_store.create("frank", "oldpassword1")
        tmp_store.change_password("frank", "newpassword2")
        # Old password fails
        with pytest.raises(InvalidCredentials):
            tmp_store.authenticate("frank", "oldpassword1")
        # New password succeeds
        u = tmp_store.authenticate("frank", "newpassword2")
        assert u["username"] == "frank"

    def test_delete(self, tmp_store):
        tmp_store.create("gina", "password1234")
        assert tmp_store.delete("gina") is True
        assert tmp_store.get("gina") is None

    def test_delete_missing(self, tmp_store):
        assert tmp_store.delete("ghost") is False

    def test_list_omits_hash(self, tmp_store):
        tmp_store.create("henry", "password1234")
        users = tmp_store.list()
        assert len(users) == 1
        assert "password_hash" not in users[0]

    def test_has_admin(self, tmp_store):
        assert tmp_store.has_admin() is False
        tmp_store.create("root", "password1234", role="admin")
        assert tmp_store.has_admin() is True

    def test_ensure_default_admin_creates_once(self, tmp_store):
        first = tmp_store.ensure_default_admin()
        assert first is not None
        assert first["username"] == "admin"
        # Second call is a no-op
        second = tmp_store.ensure_default_admin()
        assert second is None

    def test_persistence(self, tmp_store):
        from modules.auth import UserStore
        tmp_store.create("ivy", "password1234")
        # Re-open
        s2 = UserStore(tmp_store.path)
        assert s2.get("ivy") is not None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------
class TestSessionHelpers:
    def test_current_user_anonymous(self, client):
        r = client.get("/api/auth/whoami")
        assert r.status_code == 401

    def test_login_then_whoami(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        # Pre-create a user
        tmp_store.create("tester", "password1234")

        r = client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "password1234"},
        )
        assert r.status_code == 200
        body = r.get_json()
        assert body["success"] is True
        assert body["user"]["username"] == "tester"
        assert "csrf_token" in body
        # whoami now works
        r2 = client.get("/api/auth/whoami")
        assert r2.status_code == 200
        assert r2.get_json()["user"]["username"] == "tester"

    def test_login_bad_password(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("tester", "password1234")
        r = client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "wrongpassword1"},
        )
        assert r.status_code == 401

    def test_login_missing_fields(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        r = client.post("/api/auth/login", json={"username": "x"})
        assert r.status_code == 400

    def test_logout_clears_session(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("tester", "password1234")
        client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "password1234"},
        )
        r = client.post("/api/auth/logout")
        assert r.status_code == 200
        # whoami now fails
        r2 = client.get("/api/auth/whoami")
        assert r2.status_code == 401


class TestRoleGating:
    def test_admin_endpoint_blocks_user(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("alice", "password1234", role="user")
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "password1234"},
        )
        r = client.get("/api/auth/users")
        assert r.status_code == 403

    def test_admin_endpoint_allows_admin(
        self, client, tmp_store, monkeypatch
    ):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.get("/api/auth/users")
        assert r.status_code == 200
        assert r.get_json()["count"] == 1

    def test_admin_can_register_user(
        self, client, tmp_store, monkeypatch
    ):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.post(
            "/api/auth/register",
            json={
                "username": "newuser",
                "password": "newpassword1",
                "role": "user",
            },
        )
        assert r.status_code == 201
        assert tmp_store.get("newuser") is not None

    def test_register_duplicate(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        tmp_store.create("dup", "password1234")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.post(
            "/api/auth/register",
            json={"username": "dup", "password": "newpassword1"},
        )
        assert r.status_code == 409

    def test_delete_user(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        tmp_store.create("victim", "password1234", role="user")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.delete("/api/auth/users/victim")
        assert r.status_code == 200
        assert tmp_store.get("victim") is None

    def test_cannot_delete_self(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.delete("/api/auth/users/admin1")
        assert r.status_code == 400

    def test_delete_invalid_username(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.delete("/api/auth/users/has space")
        assert r.status_code == 400

    def test_delete_missing(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("admin1", "password1234", role="admin")
        client.post(
            "/api/auth/login",
            json={"username": "admin1", "password": "password1234"},
        )
        r = client.delete("/api/auth/users/ghost")
        assert r.status_code == 404


class TestPasswordChange:
    def test_change_password_success(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("tester", "oldpassword1")
        client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "oldpassword1"},
        )
        r = client.post(
            "/api/auth/password",
            json={"old_password": "oldpassword1", "new_password": "newpassword2"},
        )
        assert r.status_code == 200
        # Re-login with new password
        client.post("/api/auth/logout")
        r2 = client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "newpassword2"},
        )
        assert r2.status_code == 200

    def test_change_password_wrong_old(self, client, tmp_store, monkeypatch):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        tmp_store.create("tester", "oldpassword1")
        client.post(
            "/api/auth/login",
            json={"username": "tester", "password": "oldpassword1"},
        )
        r = client.post(
            "/api/auth/password",
            json={"old_password": "wrong", "new_password": "newpassword2"},
        )
        assert r.status_code == 401

    def test_change_password_unauthenticated(
        self, client, tmp_store, monkeypatch
    ):
        from modules import auth as a_mod
        import routes.auth as ra
        monkeypatch.setattr(a_mod, "get_default_store", lambda: tmp_store)
        monkeypatch.setattr(ra, "get_default_store", lambda: tmp_store)
        r = client.post(
            "/api/auth/password",
            json={"old_password": "x", "new_password": "y"},
        )
        assert r.status_code == 401
