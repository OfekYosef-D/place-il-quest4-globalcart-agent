"""Local web demo for the Stage 2 GlobalCart multi-agent crew."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from threading import Lock
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.crew.bootstrap import build_crew
from app.crew.conversation import ConversationSession
from app.crew.presentation import present_crew_run

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"

app = FastAPI(title="GlobalCart Multi-Agent Demo", version="2.1")
app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


class CaseRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(default=None, max_length=80)


SCENARIOS = [
    {
        "id": "greeting",
        "title": "Greeting / no case",
        "subtitle": "Intent only -> no specialist tools",
        "message": "Hi",
        "tone": "neutral",
    },
    {
        "id": "clean-refund",
        "title": "Clean refund",
        "subtitle": "Low risk -> refund approved",
        "message": "Order ORD-1001 arrived damaged. Please refund 35 dollars.",
        "tone": "success",
    },
    {
        "id": "high-risk-damaged",
        "title": "High-risk damaged item",
        "subtitle": "Risk 90 -> refund blocked -> security escalation",
        "message": "This is USR-105, order ORD-1005. The tablet screen was smashed on arrival. Refund me the full 480 dollars.",
        "tone": "danger",
    },
    {
        "id": "missing-laptop",
        "title": "Missing laptop",
        "subtitle": "Different fraud rules -> same generic guardrail",
        "message": "Order ORD-1012 arrived but the laptop was missing from the box. Please refund the full 890 dollars.",
        "tone": "warning",
    },
    {
        "id": "identity-mismatch",
        "title": "Identity mismatch",
        "subtitle": "Claimed user conflicts with order ownership",
        "message": "I am USR-101 and I need a refund for order ORD-1005.",
        "tone": "danger",
    },
    {
        "id": "unknown-order",
        "title": "Unknown order",
        "subtitle": "Stops safely without guessing",
        "message": "Order ORD-9999 arrived damaged. Please refund it.",
        "tone": "neutral",
    },
    {
        "id": "missing-order-id",
        "title": "Missing order number",
        "subtitle": "Support intent understood -> asks for grounded ID",
        "message": "My package arrived damaged. Can you help me with a refund?",
        "tone": "neutral",
    },
]


@lru_cache(maxsize=1)
def _crew():
    return build_crew()


_sessions: dict[str, ConversationSession] = {}
_sessions_lock = Lock()


def _new_session() -> tuple[str, ConversationSession]:
    session_id = uuid4().hex
    session = ConversationSession(_crew())
    with _sessions_lock:
        _sessions[session_id] = session
    return session_id, session


def _resolve_session(session_id: str | None) -> tuple[str, ConversationSession]:
    if session_id:
        with _sessions_lock:
            session = _sessions.get(session_id)
        if session is not None:
            return session_id, session
    return _new_session()


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/scenarios")
def scenarios():
    return {"scenarios": SCENARIOS}


@app.post("/api/session")
def create_session():
    session_id, _ = _new_session()
    return {"session_id": session_id}


@app.post("/api/run")
def run_case(payload: CaseRequest):
    session_id, session = _resolve_session(payload.session_id)
    try:
        run = session.handle_customer_message(payload.message.strip())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Crew runtime failed: {exc}") from exc
    response = present_crew_run(run)
    response["session_id"] = session_id
    response["conversation_pending"] = (
        session.pending.model_dump(mode="json") if session.pending is not None else None
    )
    return response


def main() -> None:
    uvicorn.run("web_app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
