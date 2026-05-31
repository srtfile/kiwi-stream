"""
Kiwi Stream API Server
======================
Run from this folder:
  python api_server.py

Then open in browser or call from any app:
  http://localhost:8000
  http://localhost:8000/streams/1535/1
  http://localhost:8000/streams/1535/1?audio=sub&quality=720p
  http://localhost:8000/docs   ← interactive Swagger UI
"""

import sys
import time
import json
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import uvicorn

# Import the extractor (same folder)
from kiwi_extractor import get_stream_urls

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Kiwi Stream API",
    description="Extract m3u8 and mp4 stream URLs from Kiwi-Stream using MAL ID",
    version="1.0.0",
)

# Allow all origins so any web app / frontend can call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Simple in-memory cache: {cache_key: (result, timestamp)}
_cache: dict = {}
CACHE_TTL = 3600  # 1 hour


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    """Simple homepage with usage examples."""
    return """
    <html>
    <head><title>Kiwi Stream API</title></head>
    <body style="font-family:monospace; padding:40px; background:#111; color:#eee">
        <h1>🎬 Kiwi Stream API</h1>
        <h3>Endpoints:</h3>
        <ul>
            <li><a href="/docs" style="color:#4af">/docs</a> — Interactive API docs (Swagger UI)</li>
            <li><a href="/streams/1535/1" style="color:#4af">/streams/{mal_id}/{episode}</a> — Get all stream URLs</li>
            <li><a href="/streams/1535/1?audio=sub&quality=720p" style="color:#4af">/streams/1535/1?audio=sub&quality=720p</a> — Filter by audio/quality</li>
            <li><a href="/health" style="color:#4af">/health</a> — Server status</li>
        </ul>
        <h3>Examples:</h3>
        <pre style="background:#222; padding:20px; border-radius:8px">
# Death Note Episode 1 - all qualities
GET /streams/1535/1

# Death Note Episode 1 - sub 720p only
GET /streams/1535/1?audio=sub&quality=720p

# Death Note Episode 2 - dub only
GET /streams/1535/2?audio=dub
        </pre>
        <p style="color:#888">Note: First request opens Chrome briefly to solve Cloudflare. Subsequent requests use cache.</p>
    </body>
    </html>
    """


@app.get("/health")
def health():
    """Check server status."""
    return {
        "status": "ok",
        "cache_entries": len(_cache),
        "message": "Kiwi Stream API is running"
    }


@app.get("/streams/{mal_id}/{episode}")
def get_streams(
    mal_id: int,
    episode: int,
    audio: Optional[str] = Query(None, description="Filter: 'sub' or 'dub'"),
    quality: Optional[str] = Query(None, description="Filter: '360p', '720p', or '1080p'"),
):
    """
    Get stream URLs for an anime episode.

    - **mal_id**: MyAnimeList anime ID (e.g. 1535 for Death Note)
    - **episode**: Episode number (e.g. 1)
    - **audio**: Optional — 'sub' or 'dub'
    - **quality**: Optional — '360p', '720p', or '1080p'
    """
    cache_key = f"{mal_id}:{episode}"
    now = time.time()

    # Return cached result if fresh
    if cache_key in _cache:
        result, cached_at = _cache[cache_key]
        age = int(now - cached_at)
        if age < CACHE_TTL:
            return _build_response(mal_id, episode, result, audio, quality,
                                   cached=True, cache_age_seconds=age)

    # Extract URLs (opens Chrome briefly for kwik.cx)
    try:
        result = get_stream_urls(mal_id, episode, verbose=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Cache it
    _cache[cache_key] = (result, now)

    return _build_response(mal_id, episode, result, audio, quality,
                           cached=False, cache_age_seconds=0)


def _build_response(mal_id, episode, result, audio, quality, cached, cache_age_seconds):
    """Build the API response, applying optional filters."""
    streams = result

    # Filter by audio type
    if audio:
        audio = audio.lower()
        if audio not in streams:
            raise HTTPException(
                status_code=404,
                detail=f"Audio '{audio}' not available. Options: {list(streams.keys())}"
            )
        streams = {audio: streams[audio]}

    # Filter by quality
    if quality:
        filtered = {}
        for a_type, qualities in streams.items():
            if quality not in qualities:
                raise HTTPException(
                    status_code=404,
                    detail=f"Quality '{quality}' not available. Options: {list(qualities.keys())}"
                )
            filtered[a_type] = {quality: qualities[quality]}
        streams = filtered

    return {
        "mal_id": mal_id,
        "episode": episode,
        "cached": cached,
        "cache_age_seconds": cache_age_seconds if cached else 0,
        "streams": streams,
    }


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  Kiwi Stream API Server")
    print("=" * 50)
    print("  URL:  http://localhost:8000")
    print("  Docs: http://localhost:8000/docs")
    print("  Test: http://localhost:8000/streams/1535/1")
    print("=" * 50)
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
