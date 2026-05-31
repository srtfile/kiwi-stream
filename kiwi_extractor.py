"""
Kiwi Stream Extractor
=====================
Extracts m3u8 and mp4 URLs from Kiwi-Stream using a MAL ID + episode number.

PURE PYTHON after first run:
  - First run: opens real Chrome once via DrissionPage to solve Cloudflare
    and saves kwik.cx cookies to kwik_cookies.json
  - All subsequent runs: uses saved cookies — NO browser needed
  - Cookies auto-refresh when expired (cf_clearance lasts ~1 year)

Flow:
  1. mapper.nekostream.site API  →  pahe short URLs
  2. curl-cffi proxy request     →  kwik.cx/f/<id>
  3. curl-cffi GET /f/<id>       →  extract _token  (uses saved cookies)
  4. curl-cffi POST /d/<id>      →  302 → vault mp4 URL
  5. Derive m3u8 from mp4 hash

Requirements:
  pip install curl-cffi DrissionPage

Usage:
  python kiwi_extractor.py
  python kiwi_extractor.py --mal-id 1535 --episode 2
  python kiwi_extractor.py --mal-id 1535 --episode 1 --json
  python kiwi_extractor.py --refresh-cookies   # force re-open browser
"""

import re
import sys
import os
import time
import json
import argparse
from pathlib import Path

from curl_cffi import requests as cffi_requests

# ── constants ─────────────────────────────────────────────────────────────────
MAPPER_API   = "https://mapper.nekostream.site/api/mal/{mal_id}/{episode}/{timestamp}"
PROXY_URL    = "https://raspy-bread-20dd.animixplaycors.workers.dev/{code}"
COOKIE_FILE  = Path(__file__).parent / "kwik_cookies.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)

CHROME_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=0, i",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "cross-site",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": UA,
}


# ── Cookie management ─────────────────────────────────────────────────────────

def load_cookies():
    """Load saved kwik.cx cookies from disk."""
    if COOKIE_FILE.exists():
        try:
            data = json.loads(COOKIE_FILE.read_text())
            return data.get("cookies", {}), data.get("expires_at", 0)
        except Exception:
            pass
    return {}, 0


def save_cookies(cookies: dict, expires_at: float):
    """Save kwik.cx cookies to disk."""
    COOKIE_FILE.write_text(json.dumps({
        "cookies": cookies,
        "expires_at": expires_at,
        "saved_at": time.time(),
    }, indent=2))


def cookies_valid(cookies: dict, expires_at: float) -> bool:
    """Check if saved cookies are still valid."""
    if not cookies:
        return False
    if not cookies.get("cf_clearance"):
        return False
    # Expire 1 day before actual expiry to be safe
    if expires_at and time.time() > (expires_at - 86400):
        return False
    return True


def fetch_fresh_cookies(verbose=True) -> dict:
    """
    Open real Chrome via DrissionPage to solve Cloudflare on kwik.cx.
    Returns fresh cookies dict. Saves them to disk for future use.
    This only needs to run ONCE (cookies last ~1 year).
    """
    from DrissionPage import ChromiumPage, ChromiumOptions

    if verbose:
        print("[cookies] Opening Chrome to solve Cloudflare (one-time setup)...")
        print("[cookies] A browser window will open briefly — please wait...")

    co = ChromiumOptions()
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-blink-features=AutomationControlled")
    profile_dir = os.path.join(os.environ.get("TEMP", "C:/Temp"), "kwik_drission_profile")
    os.makedirs(profile_dir, exist_ok=True)
    co.set_user_data_path(profile_dir)

    dp = ChromiumPage(co)
    cookies = {}
    expires_at = 0

    try:
        dp.get("https://kwik.cx/f/eXJ5bZQhmLeZ")

        # Wait up to 30s for CF to solve
        for i in range(30):
            title = dp.title
            if title and "Just a moment" not in title and "Attention Required" not in title:
                if verbose:
                    print(f"[cookies] ✓ Cloudflare solved after {i}s")
                break
            time.sleep(1)

        # Collect all kwik.cx cookies
        for c in dp.cookies():
            if 'kwik' in c.get('domain', ''):
                cookies[c['name']] = c['value']
                # Parse cf_clearance expiry
                if c['name'] == 'cf_clearance' and c.get('expires'):
                    expires_at = float(c['expires'])

        if verbose:
            print(f"[cookies] Collected: {list(cookies.keys())}")

    finally:
        dp.quit()

    if not cookies.get('cf_clearance'):
        raise RuntimeError("Failed to get cf_clearance cookie — CF challenge not solved")

    save_cookies(cookies, expires_at)
    if verbose:
        print(f"[cookies] Saved to {COOKIE_FILE}")
        print("[cookies] Future runs will use saved cookies — no browser needed")

    return cookies


def get_kwik_cookies(force_refresh=False, verbose=True) -> dict:
    """
    Get valid kwik.cx cookies.
    Uses saved cookies if valid, otherwise opens browser once to refresh.
    """
    if not force_refresh:
        cookies, expires_at = load_cookies()
        if cookies_valid(cookies, expires_at):
            if verbose:
                print(f"[cookies] Using saved cookies from {COOKIE_FILE.name}")
            return cookies

    # Need fresh cookies — open browser once
    return fetch_fresh_cookies(verbose=verbose)


# ── Step 1: mapper API ────────────────────────────────────────────────────────

def call_mapper_api(mal_id, episode, timestamp):
    url = MAPPER_API.format(mal_id=mal_id, episode=episode, timestamp=timestamp)
    resp = cffi_requests.get(
        url,
        headers={"User-Agent": UA},
        impersonate="chrome131",
        verify=False,
        timeout=20,
    )
    return json.loads(resp.text)


# ── Step 2: pahe → kwik.cx/f/<id> ────────────────────────────────────────────

def get_kwik_url(pahe_url, verbose=True):
    """pahe.nekostream.site/<code>  →  kwik.cx/f/<id>"""
    code = pahe_url.rstrip("/").split("/")[-1]
    proxy_url = PROXY_URL.format(code=code)

    if verbose:
        print(f"    proxy → {proxy_url}")

    resp = cffi_requests.get(
        proxy_url,
        headers={**CHROME_HEADERS, "referer": "https://pahe.nekostream.site/"},
        impersonate="chrome131",
        verify=False,
        timeout=20,
        allow_redirects=True,
    )

    final_url = resp.url
    if "kwik.cx" in final_url:
        kwik_url = final_url
    else:
        m = re.search(r'https?://kwik\.cx/[^\s\'"<>]+', resp.text)
        if m:
            kwik_url = m.group(0)
        else:
            meta = re.search(
                r'<meta[^>]+http-equiv=["\']refresh["\'][^>]+content=["\'][^"\']*url=\'?([^\'">\s]+)',
                resp.text, re.IGNORECASE
            )
            if meta:
                kwik_url = meta.group(1).strip("'\"")
            else:
                raise RuntimeError(
                    f"No kwik.cx URL found. Final: {final_url}\nBody[:300]: {resp.text[:300]}"
                )

    return re.sub(r'kwik\.cx/[ed]/', 'kwik.cx/f/', kwik_url)


# ── Step 3: GET /f/ → extract _token ─────────────────────────────────────────

def get_token_from_kwik(kwik_f_url, cookies, verbose=True):
    """
    GET kwik.cx/f/<id> with saved cookies → extract _token hidden input.
    Pure Python — no browser needed once cookies are saved.
    """
    if verbose:
        print(f"    GET /f/ → extract _token")

    resp = cffi_requests.get(
        kwik_f_url,
        headers={
            **CHROME_HEADERS,
            "referer": "https://pahe.nekostream.site/",
            "sec-fetch-site": "cross-site",
        },
        cookies=cookies,
        impersonate="chrome131",
        verify=False,
        timeout=20,
        allow_redirects=True,
    )

    if resp.status_code == 403:
        raise RuntimeError("kwik.cx returned 403 — cookies expired, run with --refresh-cookies")

    # Extract _token from hidden input
    token_match = re.search(
        r'<input[^>]+name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']',
        resp.text, re.I
    )
    if not token_match:
        token_match = re.search(
            r'value=["\']([^"\']+)["\'][^>]+name=["\']_token["\']',
            resp.text, re.I
        )

    if not token_match:
        raise RuntimeError(
            f"_token not found on kwik page (status {resp.status_code}). "
            "Cookies may be expired — run with --refresh-cookies"
        )

    return token_match.group(1)


# ── Step 4: POST /d/ → mp4 URL ───────────────────────────────────────────────

def post_kwik_download(kwik_f_url, token, cookies):
    """POST kwik.cx/d/<id> with _token → 302 redirect to vault mp4 URL."""
    kwik_d_url = kwik_f_url.replace('/f/', '/d/')

    resp = cffi_requests.post(
        kwik_d_url,
        headers={
            "User-Agent": UA,
            "Referer": kwik_f_url,
            "Origin": "https://kwik.cx",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "cache-control": "max-age=0",
        },
        cookies=cookies,
        data={"_token": token},
        impersonate="chrome131",
        verify=False,
        allow_redirects=False,
        timeout=20,
    )

    location = resp.headers.get('location', '')
    if location and 'vault' in location:
        return location

    vault = re.search(r'https://vault-\d+\.uwucdn\.top/mp4/[^\s\'"<>]+', resp.text)
    if vault:
        return vault.group(0)

    raise RuntimeError(
        f"POST {kwik_d_url} → {resp.status_code}, no vault URL. "
        f"Location: {location!r}"
    )


# ── Step 5: mp4 → m3u8 ───────────────────────────────────────────────────────

def mp4_to_m3u8(mp4_url):
    m = re.search(r'(vault-\d+\.uwucdn\.top)/mp4/(.+?)(?:\?|$)', mp4_url)
    if not m:
        raise RuntimeError(f"Cannot derive m3u8 from: {mp4_url}")
    return f"https://{m.group(1)}/stream/{m.group(2)}/uwu.m3u8"


# ── main extractor ────────────────────────────────────────────────────────────

def get_stream_urls(mal_id, episode, timestamp=None, verbose=True):
    """
    Full extraction pipeline.
    Uses DrissionPage (real Chrome) for kwik.cx steps — opened ONCE for all qualities.

    Returns:
    {
      "sub": {
        "360p":  {"mp4": "...", "m3u8": "..."},
        ...
      },
      "dub": { ... }
    }
    """
    if timestamp is None:
        timestamp = int(time.time())

    # Step 1: mapper API (pure Python)
    api_url = MAPPER_API.format(mal_id=mal_id, episode=episode, timestamp=timestamp)
    if verbose:
        print(f"\n[1] Mapper API: {api_url}")

    api_data = call_mapper_api(mal_id, episode, timestamp)
    kiwi = api_data.get("Kiwi-Stream")
    if not kiwi:
        raise RuntimeError(f"No 'Kiwi-Stream' in API response: {api_data}")

    # Collect all kwik URLs first (pure Python, no browser)
    kwik_jobs = []  # list of (audio_type, quality, pahe_url, kwik_url)

    for audio_type in ("sub", "dub"):
        if audio_type not in kiwi:
            continue
        for quality_key, pahe_url in kiwi[audio_type].get("download", {}).items():
            quality = quality_key.replace("Kiwi-Stream-", "")
            if verbose:
                print(f"\n[2] {audio_type} {quality}: {pahe_url}")
            try:
                kwik_url = get_kwik_url(pahe_url, verbose=verbose)
                if verbose:
                    print(f"    kwik: {kwik_url}")
                kwik_jobs.append((audio_type, quality, pahe_url, kwik_url))
            except Exception as exc:
                if verbose:
                    print(f"    ERROR (proxy): {exc}")
                kwik_jobs.append((audio_type, quality, pahe_url, None))

    # Open browser ONCE for all kwik.cx steps
    results = {}
    dp = _open_drission_browser(verbose=verbose)

    try:
        for audio_type, quality, pahe_url, kwik_url in kwik_jobs:
            if audio_type not in results:
                results[audio_type] = {}

            if kwik_url is None:
                results[audio_type][quality] = {"error": "proxy step failed"}
                continue

            if verbose:
                print(f"\n[3] {audio_type} {quality} → {kwik_url}")

            try:
                mp4_url = _kwik_get_mp4_drission(dp, kwik_url, verbose=verbose)
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
        try:
            dp.quit()
        except Exception:
            pass

    return results


def _open_drission_browser(verbose=True):
    """Open DrissionPage browser once."""
    from DrissionPage import ChromiumPage, ChromiumOptions
    if verbose:
        print("\n[browser] Opening Chrome (used for all kwik.cx requests)...")
    co = ChromiumOptions()
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-blink-features=AutomationControlled")
    profile_dir = os.path.join(os.environ.get("TEMP", "C:/Temp"), "kwik_drission_profile")
    os.makedirs(profile_dir, exist_ok=True)
    co.set_user_data_path(profile_dir)
    return ChromiumPage(co)


def _kwik_get_mp4_drission(dp, kwik_f_url, verbose=True):
    """
    Use existing DrissionPage browser to:
      1. Navigate to kwik.cx/f/<id>  (CF auto-solves with real Chrome)
      2. Extract _token
      3. POST to /d/<id>  →  vault mp4 URL
    """
    dp.get(kwik_f_url)

    # Wait for CF to solve (up to 30s)
    for _ in range(30):
        title = dp.title
        if title and "Just a moment" not in title and "Attention Required" not in title:
            break
        time.sleep(1)

    # Extract _token
    token_el = dp.ele('css:input[name="_token"]', timeout=5)
    if token_el:
        token = token_el.attr('value')
    else:
        html = dp.html
        m = re.search(r'name=["\']_token["\'][^>]+value=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'value=["\']([^"\']+)["\'][^>]+name=["\']_token["\']', html, re.I)
        if not m:
            raise RuntimeError(f"_token not found. Title: {dp.title!r}")
        token = m.group(1)

    if verbose:
        print(f"    token: {token[:20]}...")

    # Collect cookies
    cookies = {c['name']: c['value'] for c in dp.cookies() if 'kwik' in c.get('domain', '')}

    # POST to /d/
    kwik_d_url = kwik_f_url.replace('/f/', '/d/')
    resp = cffi_requests.post(
        kwik_d_url,
        headers={
            "User-Agent": UA,
            "Referer": kwik_f_url,
            "Origin": "https://kwik.cx",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "cache-control": "max-age=0",
        },
        cookies=cookies,
        data={"_token": token},
        impersonate="chrome131",
        verify=False,
        allow_redirects=False,
        timeout=20,
    )

    location = resp.headers.get('location', '')
    if location and 'vault' in location:
        return location

    vault = re.search(r'https://vault-\d+\.uwucdn\.top/mp4/[^\s\'"<>]+', resp.text)
    if vault:
        return vault.group(0)

    raise RuntimeError(f"POST {kwik_d_url} → {resp.status_code}, no vault URL")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract m3u8 and mp4 URLs from Kiwi-Stream using a MAL ID."
    )
    parser.add_argument("--mal-id",    type=int, default=1535,
                        help="MyAnimeList ID (default: 1535 = Death Note)")
    parser.add_argument("--episode",   type=int, default=1,
                        help="Episode number (default: 1)")
    parser.add_argument("--timestamp", type=int, default=None,
                        help="Optional timestamp override")
    parser.add_argument("--json",      action="store_true",
                        help="Print result as JSON only")
    args = parser.parse_args()

    verbose = not args.json

    try:
        urls = get_stream_urls(
            mal_id=args.mal_id,
            episode=args.episode,
            timestamp=args.timestamp,
            verbose=verbose,
        )
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
