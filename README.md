# VideoRAG — Creator Intelligence Platform

Full-stack RAG chatbot that ingests two social media videos, embeds their transcripts into Pinecone, and enables streaming multi-turn chat with source citations.

---

## Quick Start

### 1. Clone & configure

```bash
git clone https://github.com/your-org/video-rag
cd video-rag

# Backend
cp backend/.env.example backend/.env
# Fill in ANTHROPIC_API_KEY, COHERE_API_KEY, PINECONE_API_KEY

# Frontend
cp frontend/.env.example frontend/.env
# REACT_APP_API_URL=http://localhost:8000
```

### 2. Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 3. Frontend

```bash
cd frontend
npm install
npm start   # http://localhost:3000
```

---

## Architecture

```
Browser
  └─ React (IngestForm → VideoCards + ChatPanel)
         │
         │ POST /api/ingest   (concurrent yt-dlp + transcript extraction)
         │ POST /api/chat     (SSE streaming)
         ▼
  FastAPI (main.py)
         │
         ├─ ingest.py          yt-dlp + youtube-transcript-api
         ├─ vector_store.py    Cohere embed → Pinecone upsert/query
         └─ rag_graph.py       LangGraph: router → retrieve → Claude stream
                  │
                  ├─ Pinecone (cosine, serverless, us-east-1)
                  ├─ Cohere embed-english-v3.0
                  └─ Anthropic claude-sonnet-4-20250514
```

### Why this stack?

| Decision | Choice | Reasoning |
|---|---|---|
| **Orchestration** | LangGraph | Explicit state graph with conditional routing. Metadata-only questions skip retrieval entirely — saves ~60ms + 1 Cohere embed call per turn. Over 1000 creators/day that's ~500K embed calls/month saved. |
| **Embeddings** | Cohere `embed-english-v3.0` | High retrieval quality at low cost $0.10/1M tokens vs OpenAI's $0.13/1M. More importantly: `input_type` distinction (`search_document` vs `search_query`) gives a ~8–12% NDCG lift over symmetric embeddings with zero extra cost. |
| **Vector DB** | Pinecone Serverless | Zero idle cost amd no need to setup heavy computation device for embeddings and search. At 1000 creators/day with ~15 chat turns each: ~15K queries/day. Pinecone serverless costs ~$0.40/1M RUs. Entire day's chat = ~$0.006. ChromaDB is free but requires persistent infra; Qdrant Cloud is comparable but Pinecone has better SLA for production. |
| **LLM** | Gemini 2.5 flash / Groq | Gemini is the cheapest LLM out there when comparing rates and Groq is free tier both are good but Gemini can be covered and metered in GCP/ console along with other services if you use GCP |
| **Transcript** | youtube-transcript-api | Free, no API key, <200ms. Whisper/AssemblyAI only needed as fallback for videos without captions. |
| **Backend** | FastAPI + SSE | Native async streaming. No WebSocket overhead. SSE reconnects automatically in browsers. |

### Cost at 1000 creators/day

```
Ingest per creator (2 videos):
  Cohere embed (2 × ~10 chunks × 400 tokens) = 8K tokens = $0.0008
  Pinecone upsert (20 vectors) = negligible

Chat (15 turns × 1000 creators):
  Cohere query embed: 15K × 400 tokens = $0.0006/day total
  Pinecone query: 15K queries = ~$0.006/day
  Groq: Free tier
  Gemini[optional]

Total: ~$0.0014/day if creators less than 1000/min else shift to paid
```

**This is order-of-magnitude cheaper than any RAG-as-a-service offering.**

### What to optimize at 10× scale (10K creators/day)

1. **Batch embed on ingest** — already batched at 96 (Cohere limit). At scale, switch to async Cohere batch jobs ($0.02/1M tokens vs $0.10).
2. **Pinecone namespaces → TTL** — add a 24h TTL sweep to delete stale sessions. Reduces storage cost.
3. **Redis session store** — replace in-memory `SESSIONS` dict. Stateless FastAPI workers behind a load balancer.
4. **Whisper on-prem** — for Instagram reels without captions, self-hosted Whisper (large-v3) at $0.003/min is 10× cheaper than AssemblyAI ($0.037/min).

---

## API Reference

| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/ingest` | Extract + embed two video URLs |
| POST | `/api/chat` | Streaming SSE chat (multi-turn) |
| GET | `/api/session/{id}` | Session metadata |
| DELETE | `/api/session/{id}` | Clean up Pinecone namespace |

### POST /api/ingest

```json
{ "url_a": "https://youtube.com/...", "url_b": "https://instagram.com/reel/..." }
```

Response:
```json
{
  "session_id": "abc123",
  "video_a": { "title": "...", "views": 1200000, "engagement_rate": 4.21, ... },
  "video_b": { ... }
}
```

### POST /api/chat

```json
{ "session_id": "abc123", "message": "Why did Video A get more engagement?" }
```

SSE stream:
```
data: __SOURCES__[{"label":"A","chunk_index":0,"chunk_text":"...","score":0.921}]
data: Based on the transcript data
data: , Video A opens with a strong hook
...
data: [DONE]
```

---

## Environment Variables

See `backend/.env.example`:

```
GROQ_API_KEY=     # claude-sonnet-4
COHERE_API_KEY=        # embed-english-v3.0
PINECONE_API_KEY=      # serverless index auto-created
PINECONE_INDEX_NAME=   # default: video-rag
```

---

## Project Structure

```
video-rag/
├── backend/
│   ├── main.py           # FastAPI app + routes
│   ├── ingest.py         # yt-dlp + transcript extraction
│   ├── vector_store.py   # Cohere embed + Pinecone operations
│   ├── rag_graph.py      # LangGraph RAG pipeline
│   └── requirements.txt
└── frontend/
    ├── public/
    └── src/
        ├── App.js
        ├── lib/api.js
        └── components/
            ├── IngestForm.js
            ├── VideoCard.js
            └── ChatPanel.js
```
