"""
rag_graph.py — LangGraph 1.x RAG pipeline with langchain_google_genai streaming.

langchain_google_genai 4.x notes:
- ChatGoogleGenerativeAI(model="gemini-2.0-flash", ...) 
- Supports .astream() returning AsyncIterator[AIMessageChunk]
- Each chunk has .content (str)

Why Gemini 2.5 Flash over Claude here:
- langchain_google_genai is the mandatory stack addition per spec
- Flash: 1M token context, $0.10/$0.40 per 1M tokens — cheapest frontier model
- At scale: 15K turns/day × 1200 tokens avg = $0.002/day vs Claude's $0.13/day
"""

from __future__ import annotations

import json
import logging
import os
from typing import AsyncIterator, Literal, TypedDict
from dotenv import load_dotenv 
from langchain_google_vertexai import ChatVertexAI
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from vector_store import similarity_search

load_dotenv()

logger = logging.getLogger(__name__)

GEMINI_MODEL = "gemini-2.5-flash"


# ── State ─────────────────────────────────────────────────────────────────────

class ReelRAGState(TypedDict):
    messages: list[dict]              
    video_meta: dict[str, dict]       
    namespace: str
    retrieved_chunks: list[dict]
    route: Literal["retrieve", "direct"]
    answer: str

# keywords for routing and retrieval reducing the need for spawning one more agent for routing purpose seperately
_METADATA_KW = {
    "engagement rate", "engagement", "views", "likes", "comments",
    "follower", "followers", "creator", "upload date", "duration",
    "hashtag", "platform", "title", "who", "when", "how many",
}

_RETRIEVAL_KW = {
    "transcript", "hook", "content", "said", "suggest", "improve",
    "why", "compare", "worked", "open", "intro", "first", "second",
}

# Routing funcion that makes use of keywords to determine if it needs retrieval 
def router_node(state: ReelRAGState) -> ReelRAGState:
    q = state["messages"][-1]["content"].lower()
    needs_retrieval = any(kw in q for kw in _RETRIEVAL_KW) or not any(kw in q for kw in _METADATA_KW)
    state["route"] = "retrieve" if needs_retrieval else "direct"
    return state


# Retrieval

def retrieve_node(state: ReelRAGState) -> ReelRAGState:
    """
    Retrieves the relevant data from vector db based on query
    """
    query = state["messages"][-1]["content"]
    q_lower = query.lower()

    label_filter = None
    if "video a" in q_lower and "video b" not in q_lower:
        label_filter = "A"
    elif "video b" in q_lower and "video a" not in q_lower:
        label_filter = "B"

    state["retrieved_chunks"] = similarity_search(
        query, state["namespace"], top_k=6, filter_label=label_filter
    )
    return state


# Prompt builder based on video metadata 

def _build_system_prompt(video_meta: dict[str, dict], chunks: list[dict]) -> str:
    meta_lines = ""
    for label, m in video_meta.items():
        meta_lines += f"""
Video {label}:
  Title: {m['title']}
  Creator: {m['creator']}
  Followers: {m.get('follower_count', 'N/A'):,}
  Views: {m['views']:,}  |  Likes: {m['likes']:,}  |  Comments: {m['comments']:,}
  Engagement Rate: {m['engagement_rate']}%
  Duration: {m['duration']}s  |  Upload Date: {m['upload_date']}
  Hashtags: {m.get('hashtags', 'none')}
  Platform: {m['platform']}
  URL: {m['url']}
"""

    ctx = ""
    for i, c in enumerate(chunks):
        mm = c["metadata"]
        ctx += f"\n[SOURCE {i+1} | Video {mm['label']} | Chunk {mm['chunk_index']} | Score {c['score']:.3f}]\n{mm['chunk_text']}\n---"

    return f"""You are a social media analytics expert helping creators understand video performance.

## VIDEO METADATA
{meta_lines}

## RETRIEVED TRANSCRIPT CONTEXT
{ctx if ctx else "(Metadata question — answer directly from the data above.)"}

## INSTRUCTIONS
- Answer directly and concisely.
- Cite sources as [Video A, Chunk N] or [Video B, Chunk N].
- Use actual numbers from the metadata.
-Only answer as per the question what it needs and in proper format
- For improvement suggestions, base them on specific evidence from Video A's transcript.
- Format numbers with commas. Engagement rates to 2 decimal places.
- Always foramt the output to make it look easy to read and grasp the answer.
- Keep answers under 400 words unless depth is required.
- Format responses with proper Markdown. Always put a blank line before lists.
Output example:
Question: What is the engagement rate of each video
Answer:
The engagement rate of Video A is [engagement_rate of video A] and Video B is [enagement_rate of video B].

Question:
What are the suggestions for improvement for Video B/video A?
Answer:
[key points listing why video A/B is better in proper formatting]


"""


# ── Graph ─────────────────────────────────────────────────────────────────────

def build_graph():
    g = StateGraph(ReelRAGState)
    g.add_node("router", router_node)
    g.add_node("retrieve", retrieve_node)
    g.set_entry_point("router")
    g.add_conditional_edges(
        "router",
        lambda s: s["route"],
        {"retrieve": "retrieve", "direct": END},
    )
    g.add_edge("retrieve", END)
    return g.compile()


GRAPH = build_graph()


# ── Streaming entrypoint ──────────────────────────────────────────────────────

async def stream_response(
    user_message: str,
    conversation_history: list[dict],
    video_meta: dict[str, dict],
    namespace: str,
) -> AsyncIterator[str]:
    """
    Full pipeline: route → maybe retrieve → stream Gemini response.
    Yields SSE-ready strings:
      __SOURCES__[...json...]  — citation metadata (first event, if retrieved)
      <token>                  — streamed text
    """
    messages = conversation_history + [{"role": "user", "content": user_message}]

    state: ReelRAGState = {
        "messages": messages,
        "video_meta": video_meta,
        "namespace": namespace,
        "retrieved_chunks": [],
        "route": "retrieve",
        "answer": "",
    }

    # Run routing + optional retrieval (sync nodes, fast)
    state = GRAPH.invoke(state)

    # Emit sources first so frontend can render citations before text starts
    if state["retrieved_chunks"]:
        sources = [
            {
                "label": c["metadata"]["label"],
                "chunk_index": c["metadata"]["chunk_index"],
                "chunk_text": c["metadata"]["chunk_text"],
                "score": round(c["score"], 3),
                "title": c["metadata"]["title"],
            }
            for c in state["retrieved_chunks"]
        ]
        yield f"__SOURCES__{json.dumps(sources)}\n"

    # Build langchain_google_genai messages
    system_prompt = _build_system_prompt(state["video_meta"], state["retrieved_chunks"])

    # Convert history to LangChain message format
    lc_messages = [SystemMessage(content=system_prompt)]
    for msg in messages:
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            from langchain_core.messages import AIMessage
            lc_messages.append(AIMessage(content=msg["content"]))

    # llm = ChatVertexAI(
    #     model=GEMINI_MODEL,
    #     project='',
    #     location='',
    #     temperature=0.3,
    #     streaming=True,
    # )
    llm= ChatGroq(
        model="llama-3.1-8b-instant",
        temperature=0.3,
        max_retries=2,
        streaming=True
    )

    # astream yields AIMessageChunk objects; .content is the token string
    async for chunk in llm.astream(lc_messages):
        if chunk.content:
            yield chunk.content