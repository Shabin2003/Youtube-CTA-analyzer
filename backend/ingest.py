"""
ingest.py — Video metadata + transcript extraction pipeline.

Transcript strategy (in order):
  1. youtube-transcript-api (YouTube native captions)
  2. AssemblyAI (audio download → cloud STT) — fallback for YouTube
     when captions are disabled, and primary for Instagram/other.

Dependencies:
    pip install yt-dlp youtube-transcript-api assemblyai
"""

from __future__ import annotations

import re
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from typing import Optional

import yt_dlp
import assemblyai as aai
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# AssemblyAI API key — set via env var ASSEMBLYAI_API_KEY
aai.settings.api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")

# youtube-transcript-api 1.x is instance-based
_ytt_api = YouTubeTranscriptApi()


@dataclass
class VideoData:
    video_id: str
    label: str                    # "A" or "B"
    platform: str                 # "youtube" | "instagram"
    url: str
    title: str
    creator: str
    follower_count: int
    views: int
    likes: int
    comments: int
    duration: int
    upload_date: str
    hashtags: list[str]
    transcript: str
    engagement_rate: float = field(init=False)
    thumbnail: str = ""

    def __post_init__(self):
        safe_views = max(self.views, 1)
        self.engagement_rate = round(
            ((self.likes + self.comments) / safe_views) * 100, 4
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["hashtags"] = self.hashtags
        return d

    def metadata_for_chunks(self) -> dict:
        """Flat dict — safe for Pinecone metadata (no nested objects)."""
        return {
            "video_id": self.video_id,
            "label": self.label,
            "platform": self.platform,
            "url": self.url,
            "title": self.title,
            "creator": self.creator,
            "follower_count": self.follower_count,
            "views": self.views,
            "likes": self.likes,
            "comments": self.comments,
            "duration": self.duration,
            "upload_date": self.upload_date,
            "hashtags": ", ".join(self.hashtags),
            "engagement_rate": self.engagement_rate,
            "thumbnail": self.thumbnail,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_youtube_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"shorts/([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def _youtube_native_transcript(video_id: str) -> str:
    """
    Attempt to fetch transcript via youtube-transcript-api 1.x.
    Returns empty string if unavailable.
    """
    try:
        transcript_list = _ytt_api.list(video_id)
        try:
            t = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"])
        except NoTranscriptFound:
            t = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])

        fetched = t.fetch()
        # FetchedTranscriptSnippet.text attribute (1.x API)
        return " ".join(snip.text for snip in fetched.snippets).strip()

    except (TranscriptsDisabled, NoTranscriptFound):
        logger.info("No native transcript for video_id=%s; will use AssemblyAI.", video_id)
        return ""
    except Exception as exc:
        logger.warning("Native transcript error for %s: %s", video_id, exc)
        return ""


def _assemblyai_transcript(url: str) -> str:
    """
    Download best audio from *url* via yt-dlp into a temp file,
    then transcribe with AssemblyAI.

    Returns transcript text, or empty string on failure.
    """
    if not aai.settings.api_key:
        logger.error("ASSEMBLYAI_API_KEY is not set; cannot transcribe via AssemblyAI.")
        return ""

    with tempfile.TemporaryDirectory() as tmp_dir:
        audio_path = os.path.join(tmp_dir, "audio.%(ext)s")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": audio_path,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }
            ],
            "quiet": True,
            "no_warnings": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as exc:
            logger.error("yt-dlp audio download failed for %s: %s", url, exc)
            return ""

        # Locate the downloaded file (extension substituted by yt-dlp)
        downloaded = [
            os.path.join(tmp_dir, f)
            for f in os.listdir(tmp_dir)
            if f.startswith("audio.")
        ]
        if not downloaded:
            logger.error("Audio file not found after yt-dlp download for %s", url)
            return ""

        local_audio = downloaded[0]
        logger.info("Transcribing %s via AssemblyAI…", local_audio)

        try:
            transcriber = aai.Transcriber()
            transcript = transcriber.transcribe(local_audio)

            if transcript.status == aai.TranscriptStatus.error:
                logger.error("AssemblyAI error: %s", transcript.error)
                return ""

            return (transcript.text or "").strip()

        except Exception as exc:
            logger.error("AssemblyAI transcription failed: %s", exc)
            return ""


def _get_transcript(url: str, platform: str, video_id: str) -> str:
    """
    Full transcript resolution chain:
      1. YouTube native captions  (YouTube only)
      2. AssemblyAI               (all platforms, always the final fallback)
    """
    # --- Step 1: YouTube native captions ---
    if platform == "youtube":
        yt_id = _extract_youtube_id(url) or video_id
        native = _youtube_native_transcript(yt_id)
        if native:
            return native

    # --- Step 2: AssemblyAI ---
    logger.info("Falling back to AssemblyAI for %s", url)
    return _assemblyai_transcript(url)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_video_data(url: str, label: str) -> VideoData:
    """
    Extract all metadata + transcript for a YouTube or Instagram URL.

    Args:
        url:   Direct video URL.
        label: Caller-assigned tag ("A" or "B") written to every vector chunk.

    Returns:
        Populated VideoData instance.
    """
    is_youtube = "youtube.com" in url or "youtu.be" in url
    platform = "youtube" if is_youtube else "instagram"

    ydl_opts = {
        "skip_download": True,
        "extract_flat": False,
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info: dict = ydl.extract_info(url, download=False)

    video_id: str = info.get("id", "")
    tags: list[str] = info.get("tags") or []

    # Highest-res thumbnail
    thumbnail = ""
    thumbnails = info.get("thumbnails") or []
    if thumbnails:
        thumbnail = thumbnails[-1].get("url", "")
    if not thumbnail:
        thumbnail = info.get("thumbnail", "")

    transcript = _get_transcript(url, platform, video_id)

    return VideoData(
        video_id=video_id,
        label=label,
        platform=platform,
        url=url,
        title=info.get("title") or "Untitled",
        creator=info.get("uploader") or info.get("channel") or "Unknown",
        follower_count=info.get("channel_follower_count") or 0,
        views=info.get("view_count") or 1,
        likes=info.get("like_count") or 0,
        comments=info.get("comment_count") or 0,
        duration=info.get("duration") or 0,
        upload_date=info.get("upload_date") or "",
        hashtags=[t for t in tags if t],
        transcript=transcript,
        thumbnail=thumbnail,
    )