"""
Kiwi Stream API Server
======================
FastAPI web server that exposes the extractor as a REST API.
Uses FlareSolverr (Docker) for Cloudflare bypass.

Setup:
  pip install fastapi uvicorn

  # Start FlareSolverr first:
  docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest

  # Start the API server:
  uvicorn api_server:app --host 0.0.0.0 --port 8000

Endpoints:
  GET /streams/{mal_id}/{episode}
  GET /streams/{mal_id}/{episode}?audio=sub&quality=720p
  GET /health

Examples:
  curl http://localhost:8000/streams/1535/1
  curl http://localhost:8000/streams/1535/1?audio=sub&quality=720p
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import asyncio
import time

# Import the FlareSolverr extractor
from kiwi_extractor_flaresolverr import get_stream_urls, check_flaresolverr

app = FastAPI(
    title="Kiwi Stream API",
    description="Extract m3u8 and mp4 stream URLs from Kiwi-Stream using MAL ID",
    version="1.0.0",
)

# Allow all origins (adjust for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

# Simple in-memory cache: {cache_key: (result, timestamp)}
_cache: dict = {}
CACHE_TTL = 3600  # 1 hour — URLs are valid for ~2 hours per API response


@app.get("/health")
def health():
    """Check if the server and FlareSolverr are running."""
    fs_ok = check_flaresolverr()
    return {
        "status": "ok" if fs_ok else "degraded",
        "flaresolverr": "running" if fs_ok else "not running",
        "message": (
            "Ready" if fs_ok
            else "Start FlareSolverr: docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest"
        )
    }


@app.get("/streams/{mal_id}/{episode}")
async def get_streams(
    mal_id: int,
    episode: int,
    audio: Optional[str] = Query(None, description="Filter: 'sub' or 'dub'"),
    quality: Optional[str] = Query(None, description="Filter: '360p', '720p', or '1080p'"),
):
    """
    Get stream URLs for an anime episode.

    - **mal_id**: MyAnimeList anime ID (e.g. 1535 for Death Note)
    - **episode**: Episode number (e.g. 1)
    - **audio**: Optional filter — 'sub' or 'dub'
    - **quality**: Optional filter — '360p', '720p', or '1080p'

    Returns mp4 and m3u8 URLs for each quality/audio combination.
    """
    cache_key = f"{mal_id}:{episode}"
    now = time.time()

    # Check cache
    if cache_key in _cache:
        result, cached_at = _cache[cache_key]
        if now - cached_at < CACHE_TTL:
            return _filter_result(result, audio, quality)

    # Extract URLs (runs in thread pool to not block event loop)
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: get_stream_urls(mal_id, episode, verbose=False)
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Cache the result
    _cache[cache_key] = (result, now)

    return _filter_result(result, audio, quality)


def _filter_result(result: dict, audio: Optional[str], quality: Optional[str]) -> dict:
    """Apply optional audio/quality filters to the result."""
    if audio:
        audio = audio.lower()
        if audio not in result:
            raise HTTPException(
                status_code=404,
                detail=f"Audio type '{audio}' not found. Available: {list(result.keys())}"
            )
        result = {audio: result[audio]}

    if quality:
        filtered = {}
        for a_type, qualities in result.items():
            if quality in qualities:
                filtered[a_type] = {quality: qualities[quality]}
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Quality '{quality}' not found. Available: {list(qualities.keys())}"
                )
        result = filtered

    return {
        "mal_id": None,  # filled by caller
        "episode": None,
        "streams": result,
        "cached": False,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
