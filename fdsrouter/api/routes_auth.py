"""Login/logout and account creation.

Reachable without a session (see the auth gate in api/app.py's middleware): a request has to be
able to log in before it has one. Account creation is open only in bootstrap mode (zero accounts
yet); once the first account exists, creating another one requires being logged in already --
there is no role system (CLAUDE.md/plan doc: any logged-in user may do anything), so this is
purely "you must already be someone" rather than "you must be an admin".
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from fdsrouter.core import auth

router = APIRouter(prefix="/api/auth", tags=["auth"])

SESSION_COOKIE = "fdsrouter_session"


class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


def _public_user(user: dict) -> dict:
    return {"id": user["id"], "username": user["username"], "display_name": user["display_name"]}


@router.get("/session")
def get_session(request: Request) -> dict:
    db = request.app.state.db
    user = getattr(request.state, "user", None)
    return {
        "authenticated": user is not None,
        "user": _public_user(user) if user else None,
        "bootstrap": auth.is_bootstrap_mode(db),
    }


@router.post("/register")
def register(payload: RegisterRequest, request: Request, response: Response) -> dict:
    db = request.app.state.db
    bootstrap = auth.is_bootstrap_mode(db)
    if not bootstrap and getattr(request.state, "user", None) is None:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich, um ein Konto anzulegen")
    username = payload.username.strip()
    if not username or not payload.password:
        raise HTTPException(status_code=400, detail="Benutzername und Passwort sind erforderlich")
    if db.get_user_by_username(username) is not None:
        raise HTTPException(status_code=409, detail="Benutzername ist bereits vergeben")

    user = db.create_user(
        username=username,
        display_name=payload.display_name or username,
        password_hash=auth.hash_password(payload.password),
    )
    db.insert_audit_entry(username, "user_create")

    # Bootstrap only: creating the very first account logs it in immediately, so the person
    # doing the initial setup doesn't have to register and then separately log in. Creating an
    # additional account while already logged in must never switch the caller's own session.
    if bootstrap:
        token, _ = auth.create_session(db, user["id"])
        db.touch_last_login(user["id"])
        db.insert_audit_entry(username, "login")
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            path="/",
            max_age=int(auth.SESSION_TTL.total_seconds()),
        )
    return _public_user(user)


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict:
    db = request.app.state.db
    user = db.get_user_by_username(payload.username.strip())
    if user is None or user["password_hash"] is None or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Benutzername oder Passwort ist falsch")

    token, expires_at = auth.create_session(db, user["id"])
    db.touch_last_login(user["id"])
    db.insert_audit_entry(user["username"], "login")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        max_age=int(auth.SESSION_TTL.total_seconds()),
    )
    return _public_user(user)


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    user = getattr(request.state, "user", None)
    if token:
        request.app.state.db.delete_session(auth.hash_token(token))
    if user:
        request.app.state.db.insert_audit_entry(user["username"], "logout")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}
