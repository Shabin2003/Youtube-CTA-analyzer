"""
main.py — FastAPI backend for the Video RAG Chatbot.

Endpoints:
  POST   /api/ingest          — ingest two video URLs, store in Pinecone
  POST   /api/chat            — streaming SSE chat endpoint
  GET    /api/session/{id}    — get session metadata (video cards)
  DELETE /api/session/{id}    — clean up Pinecone namespace
  GET    /health              — liveness probe
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ingest import extract_video_data
from vector_store import embed_and_store, delete_namespace
from rag_graph import stream_response

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Video RAG Chatbot", version="2.0.0")

# ── CORS ──────────────────────────────────────────────────────────────────────
# In production set ALLOWED_ORIGINS="https://your-domain.com" in .env.
# During local dev the wildcard is fine.
_raw_origins = os.getenv("ALLOWED_ORIGINS", "*")
ALLOWED_ORIGINS: list[str] = (
    ["*"] if _raw_origins.strip() == "*"
    else [o.strip() for o in _raw_origins.split(",") if o.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=["*"],  # credentials require explicit origins
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# ── In-memory session store — swap for Redis at production scale ──────────────
# sessions[session_id] = {namespace, videos, history}
SESSIONS: dict[str, dict] = {}




class IngestRequest(BaseModel):
    url_a: str
    url_b: str

class IngestResponse(BaseModel):
    session_id: str
    video_a: dict
    video_b: dict

class ChatRequest(BaseModel):
    session_id: str
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_session(session_id: str) -> dict:
    if session_id not in SESSIONS:
        raise HTTPException(
            status_code=404,
            detail="Session not found — please ingest videos first.",
        )
    return SESSIONS[session_id]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.post("/api/ingest", response_model=IngestResponse)
async def ingest(req: IngestRequest):
    """
    Extract metadata + transcripts for two URLs concurrently,
    embed with Cohere, store in Pinecone, return session_id.
    """
    session_id = uuid.uuid4().hex
    namespace = f"session_{session_id}"

    logger.info("Session %s — ingesting A: %s", session_id, req.url_a)
    logger.info("Session %s — ingesting B: %s", session_id, req.url_b)

    loop = asyncio.get_event_loop()

    # Extract both videos concurrently
    video_a, video_b = await asyncio.gather(
        loop.run_in_executor(None, extract_video_data, req.url_a, "A"),
        loop.run_in_executor(None, extract_video_data, req.url_b, "B"),
    )

    # Embed + store both concurrently
    await asyncio.gather(
        loop.run_in_executor(None, embed_and_store, video_a, namespace),
        loop.run_in_executor(None, embed_and_store, video_b, namespace),
    )

    SESSIONS[session_id] = {
        "namespace": namespace,
        "videos": {"A": video_a.to_dict(), "B": video_b.to_dict()},
        "history": [],
    }

    logger.info("Session %s ready", session_id)
    return IngestResponse(
        session_id=session_id,
        video_a=video_a.to_dict(),
        video_b=video_b.to_dict(),
    )


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """
    Streaming SSE chat endpoint.

    Event format (each line is `data: <payload>\\n\\n`):
      __SOURCES__[...json...]   — citation array (one per turn, if retrieved)
      <token>                   — streamed text token
      [DONE]                    — stream end
      __ERROR__<message>        — error
    """
    session = _get_session(req.session_id)

    async def event_generator():
        full_response = ""
        try:
            async for chunk in stream_response(
                user_message=req.message,
                conversation_history=session["history"],
                video_meta=session["videos"],
                namespace=session["namespace"],
            ):
                if not chunk.startswith("__SOURCES__"):
                    full_response += chunk
                yield f"data: {chunk}\n\n"
        except Exception as exc:
            logger.error(
                "Stream error for session %s: %s",
                req.session_id, exc,
                exc_info=True,
            )
            yield f"data: __ERROR__{str(exc)}\n\n"
        finally:
            # Persist conversation for multi-turn memory
            session["history"].append({"role": "user", "content": req.message})
            session["history"].append({"role": "assistant", "content": full_response})
            # Keep last 20 turns (40 messages) to stay within context window
            if len(session["history"]) > 40:
                session["history"] = session["history"][-40:]
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx/proxy buffering
            "Connection": "keep-alive",
        },
    )


@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    session = _get_session(session_id)
    return {
        "session_id": session_id,
        "videos": session["videos"],
        "history_turns": len(session["history"]) // 2,
    }


@app.delete("/api/session/{session_id}")
async def delete_session_route(session_id: str):
    session = _get_session(session_id)
    delete_namespace(session["namespace"])
    del SESSIONS[session_id]
    return {"detail": "Session deleted"}


@app.get("/health")
async def health():
    return {"status": "ok", "sessions": len(SESSIONS)}
