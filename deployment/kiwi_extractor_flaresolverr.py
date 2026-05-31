"""
Kiwi Stream Extractor — FlareSolverr Version
=============================================
Uses FlareSolverr (Docker) to bypass Cloudflare on kwik.cx.
This is the recommended approach for servers, Docker, GitHub Actions, etc.

FlareSolverr runs Chrome headlessly in a container and exposes an HTTP API.
No browser needed on your machine.

Setup:
  docker run -d --name flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr:latest

Usage:
  python kiwi_extractor_flaresolverr.py --mal-id 1535 --episode 1
  python kiwi_extractor_flaresolverr.py --mal-id 1535 --episode 1 --json
"""

import re
import sys
import time
import json
import argparse

import requests  # standard requests (FlareSolverr API is not CF-protected)
from curl_cffi import requests as cffi_requests

# ── constants ─────────────────────────────────────────────────────────────────
MAPPER_API       = "https://mapper.nekostream.site/api/mal/{mal_id}/{episode}/{timestamp}"
PROXY_URL        = "https://raspy-bread-20dd.animixplaycors.workers.dev/{code}"
FLARESOLVERR_URL = "http://localhost:8191/v1"   # change if running remotely

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

CHROME_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": UA,
}

# Session-level cookie cache (FlareSolverr session persists CF cookies)
_fs_session_id = None


# ── FlareSolverr helpers ──────────────────────────────────────────────────────

def fs_create_session():
    """Create a persistent FlareSolverr session (keeps CF cookies between requests)."""
    global _fs_session_id
    resp = requests.post(FLARESOLVERR_URL, json={
        "cmd": "sessions.create",
    }, timeout=30)
    data = resp.json()
    _fs_session_id = data["session"]
    return _fs_session_id


def fs_destroy_session():
    """Destroy the FlareSolverr session."""
    global _fs_session_id
    if _fs_session_id:
        requests.post(FLARESOLVERR_URL, json={
            "cmd": "sessions.destroy",
            "session": _fs_session_id,
        }, timeout=10)
        _fs_session_id = None


def fs_get(url, session_id=None, max_timeout=60000):
    """
    GET a URL through FlareSolverr.
    Returns (response_url, response_body, cookies_dict).
    """
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": max_timeout,
    }
    if session_id:
        payload["session"] = session_id

    resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=max_timeout // 1000 + 10)
    data = resp.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr error: {data.get('message', data)}")

    solution = data["solution"]
    cookies = {c["name"]: c["value"] for c in solution.get("cookies", [])}
    return solution["url"], solution["response"], cookies


def fs_post(url, post_data, cookies, referer, session_id=None, max_timeout=30000):
    """
    POST through FlareSolverr.
    Returns (response_url, response_body).
    """
    payload = {
        "cmd": "request.post",
        "url": url,
        "postData": "&".join(f"{k}={v}" for k, v in post_data.items()),
        "maxTimeout": max_timeout,
        "headers": {
            "Referer": referer,
            "Origin": "https://kwik.cx",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    }
    if session_id:
        payload["session"] = session_id

    resp = requests.post(FLARESOLVERR_URL, json=payload, timeout=max_timeout // 1000 + 10)
    data = resp.json()

    if data.get("status") != "ok":
        raise RuntimeError(f"FlareSolverr POST error: {data.get('message', data)}")

    solution = data["solution"]
    return solution["url"], solution["response"]


def check_flaresolverr():
    """Check if FlareSolverr is running."""
    try:
        resp = requests.get(FLARESOLVERR_URL.replace("/v1", ""), timeout=5)
        return resp.status_code == 200
    except Exception:
        return False


# ── Step 1: mapper API ────────────────────────────────────────────────────────

def call_mapper_api(mal_id, episode, timestamp):
    url = MAPPER_API.format(mal_id=mal_id, episode=episode, timestamp=timestamp)
    resp = cffi_requests.get(url, headers={"User-Agent": UA},
                             impersonate="chrome131", verify=False, timeout=20)
    return json.loads(resp.text)


# ── Step 2: pahe → kwik.cx/f/<id> ────────────────────────────────────────────

def get_kwik_url(pahe_url, verbose=True):
    code = pahe_url.rstrip("/").split("/")[-1]
    proxy_url = PROXY_URL.format(code=code)
    if verbose:
        print(f"    proxy → {proxy_url}")

    resp = cffi_requests.get(
        proxy_url,
        headers={**CHROME_HEADERS, "referer": "https://pahe.nekostream.site/"},
        impersonate="chrome131", verify=False, timeout=20, allow_redirects=True,
    )
    final_url = resp.url
    if "kwik.cx" in final_url:
        kwik_url = final_url
    else:
        m = re.search(r'https?://kwik\.cx/[^\s\'"<>]+', resp.text)
        kwik_url = m.group(0) if m else None
        if not kwik_url:
            raise RuntimeError(f"No kwik.cx URL. Final: {final_url}")

    return re.sub(r'kwik\.cx/[ed]/', 'kwik.cx/f/', kwik_url)


# ── Steps 3+4: kwik.cx via FlareSolverr ──────────────────────────────────────

def get_mp4_url_via_flaresolverr(kwik_f_url, session_id, verbose=True):
    """
    Use FlareSolverr to:
      1. GET /f/<id>  →  CF solved, extract _token
      2. POST /d/<id> with _token  →  vault mp4 URL
    """
    if verbose:
        print(f"    FlareSolverr GET → {kwik_f_url}")

    final_url, body, cookies = fs_get(kwik_f_url, session_id=session_id)

    # Extract _token
    token_match = re.search(
        r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']', body, re.I
    )
    if not token_match:
        token_match = re.search(
            r'value=["\']([^"\']+)["\'][^>]+name=["\']_token["\']', body, re.I
        )
    if not token_match:
        raise RuntimeError(f"_token not found on kwik page: {kwik_f_url}")

    token = token_match.group(1)
    if verbose:
        print(f"    token: {token[:20]}...")

    # POST to /d/
    kwik_d_url = kwik_f_url.replace('/f/', '/d/')
    if verbose:
        print(f"    FlareSolverr POST → {kwik_d_url}")

    _, post_body = fs_post(
        kwik_d_url,
        post_data={"_token": token},
        cookies=cookies,
        referer=kwik_f_url,
        session_id=session_id,
    )

    # The POST response body should contain the vault URL (after redirect)
    vault = re.search(r'https://vault-\d+\.uwucdn\.top/mp4/[^\s\'"<>]+', post_body)
    if vault:
        return vault.group(0)

    # Also check for meta-refresh redirect
    meta = re.search(
        r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=\'?([^\'">\s]+)',
        post_body, re.I
    )
    if meta:
        url = meta.group(1).strip("'\"")
        if 'vault' in url:
            return url

    raise RuntimeError(f"No vault URL in POST response. Body[:300]: {post_body[:300]}")


# ── Step 5: mp4 → m3u8 ───────────────────────────────────────────────────────

def mp4_to_m3u8(mp4_url):
    m = re.search(r'(vault-\d+\.uwucdn\.top)/mp4/(.+?)(?:\?|$)', mp4_url)
    if not m:
        raise RuntimeError(f"Cannot derive m3u8 from: {mp4_url}")
    return f"https://{m.group(1)}/stream/{m.group(2)}/uwu.m3u8"


# ── main extractor ────────────────────────────────────────────────────────────

def get_stream_urls(mal_id, episode, timestamp=None, verbose=True):
    if timestamp is None:
        timestamp = int(time.time())

    # Check FlareSolverr
    if not check_flaresolverr():
        raise RuntimeError(
            "FlareSolverr is not running!\n"
            "Start it with:\n"
            "  docker run -d --name flaresolverr -p 8191:8191 "
            "ghcr.io/flaresolverr/flaresolverr:latest"
        )

    # Create a persistent session (keeps CF cookies between requests)
    session_id = fs_create_session()
    if verbose:
        print(f"[FlareSolverr] Session created: {session_id}")

    try:
        # Step 1: mapper API
        api_url = MAPPER_API.format(mal_id=mal_id, episode=episode, timestamp=timestamp)
        if verbose:
            print(f"\n[1] Mapper API: {api_url}")

        api_data = call_mapper_api(mal_id, episode, timestamp)
        kiwi = api_data.get("Kiwi-Stream")
        if not kiwi:
            raise RuntimeError(f"No 'Kiwi-Stream' in API response")

        results = {}

        for audio_type in ("sub", "dub"):
            if audio_type not in kiwi:
                continue
            results[audio_type] = {}

            for quality_key, pahe_url in kiwi[audio_type].get("download", {}).items():
                quality = quality_key.replace("Kiwi-Stream-", "")
                if verbose:
                    print(f"\n[2] {audio_type} {quality}: {pahe_url}")

                try:
                    kwik_url = get_kwik_url(pahe_url, verbose=verbose)
                    if verbose:
                        print(f"    kwik: {kwik_url}")

                    mp4_url = get_mp4_url_via_flaresolverr(kwik_url, session_id, verbose=verbose)
                    m3u8_url = mp4_to_m3u8(mp4_url)

                    if verbose:
                        print(f"    mp4:  {mp4_url}")
                        print(f"    m3u8: {m3u8_url}")

                    results[audio_type][quality] = {"mp4": mp4_url, "m3u8": m3u8_url}

                except Exception as exc:
                    if verbose:
                        print(f"    ERROR: {exc}")
                    results[audio_type][quality] = {"error": str(exc)}

    finally:
        fs_destroy_session()
        if verbose:
            print("\n[FlareSolverr] Session destroyed")

    return results


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Kiwi Stream Extractor (FlareSolverr version)"
    )
    parser.add_argument("--mal-id",    type=int, default=1535)
    parser.add_argument("--episode",   type=int, default=1)
    parser.add_argument("--timestamp", type=int, default=None)
    parser.add_argument("--json",      action="store_true")
    parser.add_argument("--flaresolverr-url", default="http://localhost:8191/v1",
                        help="FlareSolverr URL (default: http://localhost:8191/v1)")
    args = parser.parse_args()

    global FLARESOLVERR_URL
    FLARESOLVERR_URL = args.flaresolverr_url

    verbose = not args.json

    try:
        urls = get_stream_urls(args.mal_id, args.episode, args.timestamp, verbose=verbose)
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(urls, indent=2))
    else:
        print("\n" + "=" * 60)
        print("RESULTS")
        print("=" * 60)
        for audio_type, qualities in urls.items():
            print(f"\n  [{audio_type.upper()}]")
            for quality, links in qualities.items():
                print(f"    {quality}:")
                if "error" in links:
                    print(f"      ERROR: {links['error']}")
                else:
                    print(f"      mp4:  {links['mp4']}")
                    print(f"      m3u8: {links['m3u8']}")


if __name__ == "__main__":
    main()
