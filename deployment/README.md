# Kiwi Stream Extractor — Deployment Guide

## Why a browser is needed
kwik.cx uses **Cloudflare Bot Management** which requires JavaScript execution
to solve a challenge. Pure Python HTTP clients fail because Cloudflare checks
the TLS fingerprint and JS execution environment.

**Solution: FlareSolverr** — a Docker container that runs Chrome headlessly
and exposes an HTTP API. Your Python code calls FlareSolverr's API instead
of kwik.cx directly.

---

## Option 1: Docker + FlareSolverr (Recommended)

The cleanest production approach. Everything runs in containers.

```bash
# Start both services with one command:
docker-compose up -d

# Test:
curl http://localhost:8000/health
curl http://localhost:8000/streams/1535/1
curl "http://localhost:8000/streams/1535/1?audio=sub&quality=720p"
```

**API Response:**
```json
{
  "streams": {
    "sub": {
      "360p": {
        "mp4": "https://vault-11.uwucdn.top/mp4/...",
        "m3u8": "https://vault-11.uwucdn.top/stream/.../uwu.m3u8"
      },
      "720p": { ... },
      "1080p": { ... }
    },
    "dub": { ... }
  }
}
```

---

## Option 2: GitHub Actions (serverless, free)

Use GitHub Actions to run the extractor on-demand or on a schedule.
FlareSolverr runs inside the Action runner.

```yaml
# .github/workflows/get_streams.yml
name: Get Stream URLs
on:
  workflow_dispatch:
    inputs:
      mal_id:
        description: "MAL ID"
        required: true
        default: "1535"
      episode:
        description: "Episode"
        required: true
        default: "1"

jobs:
  extract:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Start FlareSolverr
        run: |
          docker run -d --name flaresolverr -p 8191:8191 \
            ghcr.io/flaresolverr/flaresolverr:latest
          sleep 10  # wait for startup

      - name: Install Python deps
        run: pip install curl-cffi requests

      - name: Extract URLs
        run: |
          python deployment/kiwi_extractor_flaresolverr.py \
            --mal-id ${{ inputs.mal_id }} \
            --episode ${{ inputs.episode }} \
            --json > streams.json
          cat streams.json

      - name: Upload result
        uses: actions/upload-artifact@v4
        with:
          name: streams
          path: streams.json
```

---

## Option 3: VPS / Cloud Server

Deploy on any Linux server (AWS EC2, DigitalOcean, etc.):

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Clone your repo
git clone https://github.com/yourname/kiwi-stream
cd kiwi-stream/deployment

# 3. Start everything
docker-compose up -d

# 4. Your API is live at http://your-server-ip:8000
```

---

## Option 4: Local use (current working solution)

```bash
# Uses DrissionPage (real Chrome) — opens browser briefly
python kiwi_extractor.py --mal-id 1535 --episode 1
python kiwi_extractor.py --mal-id 1535 --episode 1 --json
```

---

## Architecture Summary

```
Client Request
    │
    ▼
FastAPI Server (api_server.py)
    │
    ├── curl-cffi ──────────────────► mapper.nekostream.site API
    │                                  (pure Python, no CF)
    │
    ├── curl-cffi ──────────────────► animixplaycors proxy
    │   + Referer header               (pure Python, no CF)
    │
    └── FlareSolverr HTTP API ──────► kwik.cx/f/<id>  (CF protected)
            │                          kwik.cx/d/<id>  (POST)
            │
            ▼
        vault-11.uwucdn.top
            ├── /mp4/...    ← direct download
            └── /stream/... ← HLS m3u8 stream
```

---

## Files

| File | Purpose |
|------|---------|
| `kiwi_extractor.py` | Local use — DrissionPage (real Chrome) |
| `kiwi_extractor_flaresolverr.py` | Server use — FlareSolverr API |
| `api_server.py` | FastAPI REST server |
| `docker-compose.yml` | Start FlareSolverr + API server |
| `Dockerfile` | Build the API server image |
| `requirements.txt` | Python dependencies |
| `.github/workflows/` | GitHub Actions workflows |
