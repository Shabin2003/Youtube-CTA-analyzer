"""
vector_store.py — Cohere embeddings + Pinecone 9.x storage/retrieval.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Optional
from dotenv import load_dotenv
import cohere
from pinecone import Pinecone, ServerlessSpec
from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingest import VideoData

load_dotenv()

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1024           # Cohere embed-english-v3.0
INDEX_NAME = os.getenv("PINECONE_INDEX_NAME", "video-rag")
COHERE_MODEL = "embed-english-v3.0"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 50


def _get_pinecone_index():
    pc = Pinecone(api_key=os.environ["PINECONE_API_KEY"])

    # IndexList.names() returns list[str] in Pinecone 9.x
    existing_names: list[str] = pc.list_indexes().names()

    if INDEX_NAME not in existing_names:
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBEDDING_DIM,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1"),
        )
        # IndexModel.status.ready is a bool in 9.x
        while not pc.describe_index(INDEX_NAME).status.ready:
            time.sleep(20)

    return pc.Index(INDEX_NAME)


def _cohere_embed(texts: list[str], input_type: str) -> list[list[float]]:
    co = cohere.Client(os.environ["COHERE_API_KEY"])
    all_embeddings: list[list[float]] = []

    # If you have pro or subscription for higher dimension embeddings 
    # you can directly use it I use this on a cohere trial so 96 max text per time

    for i in range(0, len(texts), 96):
        resp = co.embed(
            model=COHERE_MODEL,
            texts=texts[i : i + 96],
            input_type=input_type,
            truncate="END",
        )
        all_embeddings.extend(resp.embeddings)
    return all_embeddings


def _chunk_id(video_id: str, chunk_index: int) -> str:
    raw = f"{video_id}::{chunk_index}"
    return hashlib.md5(raw.encode()).hexdigest()[:12] + f"_{chunk_index}"


def embed_and_store(video: VideoData, namespace: str) -> int:
    """Chunk transcript, embed, upsert to Pinecone. Returns chunk count."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    # Prepend a context header to every chunk so the embedding carries
    # video-identity signal even in short transcript excerpts
    header = (
        f"[Video {video.label}] Title: {video.title}. "
        f"Creator: {video.creator}. Platform: {video.platform}. "
        f"Engagement rate: {video.engagement_rate}%. Transcript: "
    )

    if video.transcript.strip():
        raw_chunks = splitter.split_text(video.transcript)
    else:
        raw_chunks = ["[No transcript available for this video]"]

    texts_to_embed = [header + chunk for chunk in raw_chunks]

    logger.info("Embedding %d chunks for Video %s (%s)", len(raw_chunks), video.label, video.video_id)
    embeddings = _cohere_embed(texts_to_embed, input_type="search_document")

    index = _get_pinecone_index()
    meta_base = video.metadata_for_chunks()

    # Pinecone 9.x upsert: vectors is a sequence of dicts/tuples
    vectors = [
        {
            "id": _chunk_id(video.video_id, i),
            "values": emb,
            "metadata": {
                **meta_base,
                "chunk_index": i,
                "chunk_text": chunk,
                "total_chunks": len(raw_chunks),
            },
        }
        for i, (chunk, emb) in enumerate(zip(raw_chunks, embeddings))
    ]

    # upsert in batches of 100; show_progress=False to suppress tqdm in server logs
    for i in range(0, len(vectors), 100):
        index.upsert(
            vectors=vectors[i : i + 100],
            namespace=namespace,
            show_progress=False,
        )

    logger.info("Stored %d vectors in namespace '%s'", len(vectors), namespace)
    return len(vectors)


def similarity_search(
    query: str,
    namespace: str,
    top_k: int = 6,
    filter_label: Optional[str] = None,
) -> list[dict]:
    """
    Embed query + retrieve top_k chunks from Pinecone.
    Returns list of {score, metadata} dicts.
    """
    query_vec = _cohere_embed([query], input_type="search_query")[0]
    index = _get_pinecone_index()

    pinecone_filter = {"label": {"$eq": filter_label}} if filter_label else None

    result = index.query(
        vector=query_vec,
        top_k=top_k,
        namespace=namespace,
        include_metadata=True,
        filter=pinecone_filter,
    )

    return [
        {"score": match["score"], "metadata": match["metadata"]}
        for match in result.get("matches", [])
    ]


def delete_namespace(namespace: str) -> None:
    try:
        index = _get_pinecone_index()
        index.delete(delete_all=True, namespace=namespace)
        logger.info("Deleted namespace '%s'", namespace)
    except Exception as exc:
        logger.warning("Could not delete namespace %s: %s", namespace, exc)