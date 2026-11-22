#!/usr/bin/env python3
"""Pull every Reel and its insights into data/reels.db.

Run this on a schedule. Instagram only serves ~30 days of insight history at
a time and account metrics only ~90 days, so the daily snapshot is the whole
point: it is the history no SaaS tool will hand back to you.

Usage:
    python3 sync.py                # sync everything
    python3 sync.py --limit 20     # only the 20 most recent items
    python3 sync.py --refresh      # extend the access token by 60 days
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode

import igapi
import store

FIELDS = (
    "id,caption,media_type,media_product_type,permalink,"
    "thumbnail_url,timestamp,like_count,comments_count"
)


def preview(text, width=58):
    text = " ".join((text or "").split())
    return text[: width - 1] + "..." if len(text) > width else text.ljust(width)


def refresh_token():
    """Exchange an Instagram Login token for a fresh 60-day one."""
    if "graph.facebook.com" in igapi.BASE:
        raise SystemExit(
            "This token is a business system-user token (Unified / Facebook Login).\n"
            "Those are not refreshed with ig_refresh_token. If it stops working, generate\n"
            "a new one: API setup with Unified login > Generate access tokens."
        )

    payload = igapi._request(
        f"{igapi.BASE}/refresh_access_token",
        {"grant_type": "ig_refresh_token", "access_token": igapi.TOKEN},
    )
    token = payload.get("access_token")
    if not token:
        raise SystemExit(f"No token in response: {payload}")

    env_path = igapi.ENV_PATH
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith("IG_ACCESS_TOKEN"):
            lines[index] = f"IG_ACCESS_TOKEN={token}"
            replaced = True
            break
    if not replaced:
        lines.append(f"IG_ACCESS_TOKEN={token}")
    env_path.write_text("\n".join(lines) + "\n")

    days = round(payload.get("expires_in", 0) / 86400)
    print(f"Token refreshed and written to .env -- valid for ~{days} days.")


def check_permissions():
    """Print which scopes this token was actually granted."""
    try:
        payload = igapi.get("me/permissions")
    except igapi.ApiError as exc:
        raise SystemExit(f"Could not read permissions: {exc}")

    rows = payload.get("data") or []
    if not rows:
        raise SystemExit("The token reports no permissions at all. Generate a new token.")

    granted = [r.get("permission") for r in rows if r.get("status") == "granted"]
    for row in sorted(rows, key=lambda r: r.get("permission") or ""):
        mark = "YES" if row.get("status") == "granted" else "no "
        print(f"  [{mark}] {row.get('permission')}")

    has_insights = any("insights" in name for name in granted)
    print()
    if has_insights:
        print("Insights permission present.")
    else:
        print("MISSING: no insights permission on this token.")
        print("Editing the configuration does NOT change an existing token -- you must")
        print("generate a new one after adding instagram_manage_insights:")
        print("  API setup with Unified login > Configure your app > open the config preview")
        print("  > Edit permissions > add instagram_manage_insights > save")
        print("  > Generate access tokens > Generate token > copy the new token")


def sync_account(connection, ig_user_id):
    """Best-effort account-level snapshot; missing metrics are not fatal.

    The API rejects the whole call if period metrics (reach) and total_value
    metrics (views, accounts_engaged, total_interactions) are mixed, so they
    go out as two requests and get merged.
    """
    values = {}

    try:
        payload = igapi.get(
            f"{ig_user_id}/insights",
            {"metric": ",".join(igapi.ACCOUNT_METRICS), "period": "day"},
        )
        values.update(igapi.parse_insights(payload))
    except igapi.ApiError as exc:
        print(f"Account period metrics skipped ({exc})")

    try:
        payload = igapi.get(
            f"{ig_user_id}/insights",
            {
                "metric": ",".join(igapi.ACCOUNT_TOTAL_METRICS),
                "period": "day",
                "metric_type": "total_value",
            },
        )
        values.update(igapi.parse_insights(payload))
    except igapi.ApiError as exc:
        print(f"Account total metrics skipped ({exc})")

    if values:
        store.save_account_insights(connection, store.now_iso(), values)
        print("Account: " + ", ".join(f"{k}={v}" for k, v in values.items()))
    else:
        print("Account insights skipped (no metrics returned)")


def sync_reels(connection, ig_user_id, limit):
    taken_at = store.now_iso()
    url = None
    params = {"fields": FIELDS, "limit": 50}
    seen = 0
    no_insights = 0

    while True:
        payload = igapi.get_url(url) if url else igapi.get(f"{ig_user_id}/media", params)
        items = payload.get("data", [])
        if not items:
            break

        for item in items:
            store.upsert_media(connection, item)
            values = igapi.parse_insights(igapi.media_insights(item["id"]))
            store.save_insights(connection, taken_at, item["id"], values)
            seen += 1
            if not values:
                no_insights += 1

            kind = (item.get("media_product_type") or item.get("media_type") or "?").lower()
            views = values.get("views")
            skip = igapi.as_percent(values.get("reels_skip_rate"))
            skip_text = f"skip={skip:.0f}%" if isinstance(skip, (int, float)) else "skip=n/a"
            print(
                f"  {(item.get('timestamp') or '')[:10]} {kind:<5} "
                f"views={views if views is not None else 'n/a':<8} {skip_text:<10} {preview(item.get('caption'))}"
            )

            if limit and seen >= limit:
                break

        connection.commit()
        url = (payload.get("paging") or {}).get("next")
        if not url or (limit and seen >= limit):
            break

    return seen, no_insights


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="max media items to sync (0 = all)")
    parser.add_argument("--refresh", action="store_true", help="refresh the access token instead")
    parser.add_argument("--check", action="store_true", help="show which scopes the token has")
    args = parser.parse_args()

    igapi.require_token()

    if args.check:
        check_permissions()
        return

    if args.refresh:
        refresh_token()
        return

    connection = store.connect()
    me = igapi.whoami()
    ig_user_id = os.environ.get("IG_USER_ID", "").strip() or me["id"]

    account_type = me.get("account_type") or "professional"
    count = f", {me['media_count']} media items" if me.get("media_count") else ""
    print(f"@{me.get('username') or ig_user_id} -- {account_type}{count}")
    if account_type.lower() == "personal":
        print("! Personal account: switch to Business or Creator, insights need it.")

    sync_account(connection, ig_user_id)
    count, no_insights = sync_reels(connection, ig_user_id, args.limit)
    connection.commit()
    connection.close()

    if no_insights:
        print(f"\n{no_insights}/{count} items returned no insights at all.")
        print("That is a permission problem, not a data problem. Run:  python3 sync.py --check")

    print(f"\nSynced {count} item(s) at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC -> {store.DB_PATH}")
    print("Next: python3 report.py")


if __name__ == "__main__":
    sys.exit(main())
