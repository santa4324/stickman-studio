"""Minimal Instagram Graph API client for Reels insights.

Standard library only. Reads credentials from .env next to this file.

Targets the "Instagram API with Instagram Login" setup, which needs only a
Business or Creator Instagram account -- no Facebook Page.

Docs: https://developers.facebook.com/docs/instagram-platform/insights
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"


def load_env():
    """Load .env into os.environ without overwriting real env vars."""
    if not ENV_PATH.exists():
        return
    for raw in ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

API_VERSION = os.environ.get("IG_API_VERSION", "v27.0").strip() or "v27.0"
GRAPH_HOST = os.environ.get("IG_GRAPH_HOST", "graph.facebook.com").strip() or "graph.facebook.com"
BASE = f"https://{GRAPH_HOST}/{API_VERSION}"
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()

# Reels-era metric names. `plays`, `clips_replays_count` and `impressions`
# were retired in April 2025 -- do not add them back.
CORE_METRICS = [
    "views",
    "reach",
    "likes",
    "comments",
    "saved",
    "shares",
    "reposts",
    "total_interactions",
]

REELS_ONLY_METRICS = [
    "reels_skip_rate",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
]

# Account metrics split by how the API wants them requested. `reach` is a
# period metric; the rest only answer with metric_type=total_value, and sending
# them together fails the whole call with (#100) metric_type=total_value.
ACCOUNT_METRICS = ["reach"]
ACCOUNT_TOTAL_METRICS = ["views", "accounts_engaged", "total_interactions"]


class ApiError(RuntimeError):
    def __init__(self, status, payload):
        self.status = status
        message = payload
        if isinstance(payload, dict):
            error = payload.get("error") or {}
            message = error.get("message") or payload
        super().__init__(f"HTTP {status}: {message}")


def require_token():
    if not TOKEN:
        raise SystemExit(
            "No IG_ACCESS_TOKEN found.\n"
            "Copy .env.example to .env and paste your token (see README step 2)."
        )


def _request(url, params=None, retries=3):
    if params:
        separator = "&" if "?" in url else "?"
        url = url + separator + urllib.parse.urlencode(params)

    last_error = None
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": "reels-analyzer/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            try:
                payload = json.loads(body)
            except ValueError:
                payload = body
            last_error = ApiError(exc.code, payload)
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise last_error
        except urllib.error.URLError as exc:
            last_error = ApiError(0, str(exc.reason))
            if attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise last_error
    raise last_error


def get(path, params=None):
    """GET a Graph API path, e.g. get('me', {'fields': 'id,username'})."""
    require_token()
    payload = dict(params or {})
    payload["access_token"] = TOKEN
    return _request(f"{BASE}/{path.lstrip('/')}", payload)


def get_url(url):
    """Follow a paging.next URL, which already carries the access token."""
    return _request(url)


def whoami():
    """Find the Instagram account behind this token.

    Tokens behave differently per setup:
      - Unified Login / Facebook Login: the token belongs to a business
        integration system user, so /me is NOT the Instagram account. The
        accounts are listed under /me/assigned_instagram_accounts.
      - Instagram Login: /me answers directly.
    """
    try:
        me = get("me", {"fields": "id,username,account_type,media_count"})
        if me.get("username"):
            return me
    except ApiError:
        pass

    try:
        listing = get("me/assigned_instagram_accounts", {"fields": "id,username"})
    except ApiError as exc:
        raise SystemExit(
            f"Could not find an Instagram account for this token ({exc}).\n"
            "Check that instagram_basic is granted and that the account was added\n"
            "as an Instagram Tester in API setup with Unified login."
        )

    accounts = listing.get("data") or []
    if not accounts:
        raise SystemExit(
            "This token is not assigned any Instagram accounts.\n"
            "In the dashboard: API setup with Unified login > Generate access tokens >\n"
            "Add account, accept the tester invite in Instagram, then generate a new token."
        )

    first = accounts[0]
    return {
        "id": first["id"],
        "username": first.get("username"),
        "account_type": "professional",
        "media_count": None,
    }


def media_insights(media_id):
    """Fetch every metric we can get for one media item.

    Instagram rejects the whole request if any single metric is unavailable
    for that media type, so fall back to the core set and probe the
    Reels-only metrics one at a time.
    """
    try:
        return get(f"{media_id}/insights", {"metric": ",".join(CORE_METRICS + REELS_ONLY_METRICS)})
    except ApiError:
        pass

    collected = {"data": []}
    try:
        collected = get(f"{media_id}/insights", {"metric": ",".join(CORE_METRICS)})
    except ApiError as exc:
        print(f"    ! insights failed for {media_id}: {exc}")

    for metric in REELS_ONLY_METRICS:
        try:
            result = get(f"{media_id}/insights", {"metric": metric})
            collected.setdefault("data", []).extend(result.get("data", []))
        except ApiError:
            continue
    return collected


def parse_insights(payload):
    """Flatten an insights response into {metric_name: number}."""
    values = {}
    for item in payload.get("data", []):
        name = item.get("name")
        if not name:
            continue
        total = item.get("total_value")
        if isinstance(total, dict) and "value" in total:
            values[name] = total["value"]
            continue
        series = item.get("values") or []
        if series:
            value = series[0].get("value")
            if isinstance(value, dict):
                value = value.get("value")
            values[name] = value
    return values


def as_percent(value):
    """Skip rate arrives as a ratio or a percentage depending on version."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number * 100 if number <= 1 else number
