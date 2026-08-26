"""Phase 14: a small API server bridging the React demo UI to the guarded
multi-agent pipeline.

    Browser (WebSocket) <-> app/server.py <-> guarded_investigation.investigate_guarded()

One WebSocket endpoint, /ws/investigate: the client sends {"question",
"role"}, and the server streams every pipeline event (input guardrails,
RBAC, agent selection, tool calls/results, findings, output guardrails,
then a final "done" carrying the redacted report + full trace) as it
happens, via the on_event hook added to investigate_guarded() /
investigate_with_trace() / InvestigationTrace - this file adds zero new
security logic of its own, it only forwards what guardrails.py/
capabilities.py already decided.

Run with:  uvicorn app.server:app --reload
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import security
from app.database import auth_repository
from app.guarded_investigation import investigate_guarded
from app.guardrails import capabilities, guardrails
from app.guardrails.guardrails import DEFAULT_ROLE, ROLE_DOMAIN_TABLE
from app.rag.ingest import get_client as get_qdrant_client
from app.rag.ingest import get_embedding_model

app = FastAPI(title="Manufacturing Investigator API")

# The Vite dev server's default origin(s), always allowed for local dev.
# CORS_ORIGINS (comma-separated) adds more - only needed if the frontend is
# deployed to a *different* origin than this API. This API has no auth of
# its own beyond login sessions (app/security.py) - it's a demo harness in
# front of an already-guarded pipeline - so CORS is intentionally scoped to
# explicit origins, never "*". allow_credentials is required so the browser
# attaches/accepts the httpOnly session cookie.
#
# Note: a cross-origin frontend also needs the session cookie itself set
# SameSite=None; Secure (see the /api/auth/login handler below) - browsers
# never send a SameSite=Lax cookie on a cross-site request at all, no CORS
# setting changes that. The simplest deployment avoids this class of issue
# entirely: build the frontend (`npm run build`) and let this same app
# serve it same-origin - see the StaticFiles mount at the bottom of this
# file - in which case CORS_ORIGINS is never needed.
_EXTRA_ORIGINS = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        *_EXTRA_ORIGINS,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSION_COOKIE_NAME = "session_token"
SESSION_MAX_AGE_SECONDS = int(security.SESSION_TTL.total_seconds())


@app.on_event("startup")
def _ensure_login_accounts() -> None:
    created = auth_repository.ensure_default_users()
    if created:
        print(f"[investigator] Created default login account(s): {', '.join(created)} (see app/database/auth_repository.py).")


@app.on_event("startup")
def _warm_rag_pipeline() -> None:
    """Load the fastembed ONNX model and open the Qdrant client once, here,
    instead of lazily on whichever user's request happens to be the first
    knowledge search - that first-ever embed/connect cost is a one-time
    several-second tax (measured), and paying it at server boot means every
    real investigation sees the fast, already-warm path.
    """
    get_qdrant_client()
    get_embedding_model()


def _session_from_token(token: str | None) -> dict[str, str] | None:
    if not token:
        return None
    return security.decode_session_token(token)


def _require_session(request: Request) -> dict[str, str]:
    session = _session_from_token(request.cookies.get(SESSION_COOKIE_NAME))
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return session


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, response: Response) -> dict[str, str]:
    user = auth_repository.get_user_by_username(payload.username.strip())
    # Generic message either way - don't reveal whether the username exists.
    if user is None or not security.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    token = security.create_session_token(user.username, user.role)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,
        path="/",
    )
    return {"username": user.username, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, str]:
    session = _require_session(request)
    return {"username": session["sub"], "role": session["role"]}


@app.get("/api/admin/audit-log")
def admin_audit_log(request: Request) -> dict[str, Any]:
    session = _require_session(request)
    if session["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin role required.")

    entries = [
        {"source": "guardrail", **d.to_dict()} for d in guardrails.get_audit_log()
    ] + [
        {"source": "capability", **d.to_dict()} for d in capabilities.get_audit_log()
    ]
    entries.sort(key=lambda e: e["timestamp"])
    return {"entries": entries}


@app.get("/api/roles")
def roles() -> dict[str, Any]:
    return {
        "roles": [
            {"role": role, "domains": sorted(domains)}
            for role, domains in ROLE_DOMAIN_TABLE.items()
        ],
        "default": DEFAULT_ROLE,
    }


@app.websocket("/ws/investigate")
async def ws_investigate(websocket: WebSocket) -> None:
    session = _session_from_token(websocket.cookies.get(SESSION_COOKIE_NAME))
    if session is None:
        await websocket.close(code=4401, reason="Not authenticated.")
        return

    await websocket.accept()
    try:
        while True:
            payload = await websocket.receive_json()
            question = str(payload.get("question", "")).strip()
            role = str(payload.get("role") or DEFAULT_ROLE)

            if not question:
                await websocket.send_json({"type": "error", "message": "Question is required."})
                continue

            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            def emit(event: dict[str, Any]) -> None:
                queue.put_nowait(event)

            async def pump() -> None:
                """Serialize every queued event onto the one WebSocket
                connection, in the order it was produced, stopping after
                the pipeline's own terminal "done" event.
                """
                while True:
                    event = await queue.get()
                    await websocket.send_json(event)
                    if event.get("type") == "done":
                        return

            pump_task = asyncio.create_task(pump())
            try:
                await investigate_guarded(question, user_role=role, on_event=emit)
            except Exception as exc:  # noqa: BLE001 - report to the UI, then keep the socket open
                queue.put_nowait({"type": "error", "message": str(exc)})
                queue.put_nowait({"type": "done", "allowed": False, "stage_blocked": "error", "report": None, "trace": None})
            await pump_task
    except WebSocketDisconnect:
        pass


# Serves the built frontend (frontend/dist, from `npm run build`) when it
# exists - the deployed-as-one-app path (e.g. a single Render/Fly.io
# service), so the browser talks to the API/WebSocket same-origin and the
# session cookie's SameSite=Lax still works with no CORS involved at all.
# Mounted last so it never shadows the API/WebSocket routes above:
# Starlette tries routes in registration order, and this Mount only catches
# whatever none of them matched. Absent in local dev (frontend/dist doesn't
# exist until you build it), where the separate Vite dev server on :5173 is
# used instead - see CORSMiddleware above for that path.
_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
