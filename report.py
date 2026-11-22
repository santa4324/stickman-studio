#!/usr/bin/env python3
"""Rank your Reels against your own baseline and write data/report.md + data/reels.json.

Views are a vanity metric -- a Reel with 500k views can be worse for your
account than one with 20k. This scores each Reel on the signals that actually
drive distribution, all measured relative to your own trailing cohort:

    skip rate (lower is better)  -> did the first 3 seconds hold them
    watch-through                -> did the body retain
    shares per 1k views          -> strongest distribution signal
    saves per 1k views           -> intent to return

Usage:
    python3 report.py                 # all synced Reels
    python3 report.py --days 30       # only the last 30 days
    python3 report.py --top 5         # how many best/worst to spell out
"""

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

import igapi
import store

WEIGHTS = {
    "skip": 0.30,
    "watch_through": 0.25,
    "shares_per_1k": 0.25,
    "saves_per_1k": 0.20,
}

HIGHER_IS_BETTER = {
    "skip": False,
    "watch_through": True,
    "shares_per_1k": True,
    "saves_per_1k": True,
}


def percentile_rank(values, value, higher_is_better):
    """Rank one value inside its cohort, 0-100. Robust to outliers."""
    usable = [v for v in values if v is not None]
    if len(usable) < 2:
        return None
    if higher_is_better:
        below = sum(1 for v in usable if v < value)
    else:
        below = sum(1 for v in usable if v > value)
    equal = sum(1 for v in usable if v == value)
    return (below + 0.5 * (equal - 1)) / (len(usable) - 1) * 100


def per_k(value, views):
    if value is None or not views:
        return None
    return value / views * 1000


def build_rows(connection, days):
    insights = store.latest_insights(connection)
    enriched = store.all_enriched(connection)
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400

    rows = []
    for media in store.all_media(connection):
        metrics = insights.get(media["id"], {})
        if not metrics:
            continue
        try:
            posted = datetime.fromisoformat((media["timestamp"] or "").replace("Z", "+00:00"))
        except ValueError:
            posted = None
        if cutoff and posted and posted.timestamp() < cutoff:
            continue

        views = metrics.get("views")
        reach = metrics.get("reach")
        skip = igapi.as_percent(metrics.get("reels_skip_rate"))
        watch_ms = metrics.get("ig_reels_avg_watch_time")
        watch_s = watch_ms / 1000 if isinstance(watch_ms, (int, float)) else None
        extra = enriched.get(media["id"], {})
        duration = extra.get("duration_s")

        row = {
            "id": media["id"],
            "permalink": media["permalink"],
            "posted": (media["timestamp"] or "")[:10],
            "age_days": round((datetime.now(timezone.utc) - posted).days, 1) if posted else None,
            "caption": " ".join((media["caption"] or "").split())[:90],
            "views": views,
            "reach": reach,
            "skip": skip,
            "avg_watch_s": round(watch_s, 2) if watch_s else None,
            "duration_s": duration,
            "watch_through": round(watch_s / duration * 100, 1) if watch_s and duration else None,
            "saves": metrics.get("saved"),
            "shares": metrics.get("shares"),
            "reposts": metrics.get("reposts"),
            "likes": metrics.get("likes"),
            "comments": metrics.get("comments"),
            "shares_per_1k": per_k(metrics.get("shares"), views),
            "saves_per_1k": per_k(metrics.get("saved"), views),
            "engagement_rate": round(
                sum(metrics.get(k) or 0 for k in ("likes", "comments", "saved", "shares"))
                / reach
                * 100,
                2,
            ) if reach else None,
            "hook_text": extra.get("hook_text"),
            "wpm": extra.get("wpm"),
        }
        rows.append(row)
    return rows


def score_rows(rows):
    cohort = {
        key: [row[key] for row in rows if row.get(key) is not None] for key in WEIGHTS
    }
    for row in rows:
        parts = {}
        for key, weight in WEIGHTS.items():
            value = row.get(key)
            if value is None:
                continue
            rank = percentile_rank(cohort[key], value, HIGHER_IS_BETTER[key])
            if rank is not None:
                parts[key] = (rank, weight)
        total_weight = sum(weight for _, weight in parts.values())
        row["score"] = (
            round(sum(rank * weight for rank, weight in parts.values()) / total_weight, 1)
            if total_weight
            else None
        )
    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0)))
    return rows


def fmt(value, suffix="", digits=0):
    if value is None:
        return "--"
    if isinstance(value, str):
        return value
    return f"{value:,.{digits}f}{suffix}"


def table(rows):
    header = (
        "| # | posted | views | reach | skip | watch | thru | saves/1k | shares/1k | ER | score | caption |\n"
        "|---|--------|-------|-------|------|-------|------|----------|-----------|----|-------|---------|"
    )
    lines = [header]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| {rank} | {posted} | {views} | {reach} | {skip} | {watch} | {thru} | {saves} | {shares} | {er} | {score} | {caption} |".format(
                rank=index,
                posted=row["posted"] or "--",
                views=fmt(row["views"]),
                reach=fmt(row["reach"]),
                skip=fmt(row["skip"], "%"),
                watch=fmt(row["avg_watch_s"], "s", 1),
                thru=fmt(row["watch_through"], "%", 0),
                saves=fmt(row["saves_per_1k"], "", 1),
                shares=fmt(row["shares_per_1k"], "", 1),
                er=fmt(row["engagement_rate"], "%", 1),
                score=fmt(row["score"], "", 1),
                caption=(row["caption"] or "").replace("|", "/")[:60],
            )
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=0, help="only Reels from the last N days")
    parser.add_argument("--top", type=int, default=5, help="how many best/worst to list")
    args = parser.parse_args()

    connection = store.connect()
    rows = build_rows(connection, args.days)
    connection.close()

    if not rows:
        raise SystemExit(
            "No data yet. Run `python3 sync.py` first, then re-run this."
        )

    rows = score_rows(rows)

    lines = [f"# Reels analysis -- {len(rows)} Reels, generated {store.now_iso()[:16]} UTC", ""]

    available = {key: [r[key] for r in rows if r.get(key) is not None] for key in WEIGHTS}
    lines.append("## Your baseline (median)")
    lines.append("")
    for key, label in [
        ("skip", "skip rate (first 3s)"),
        ("watch_through", "watch-through"),
        ("avg_watch_s", "avg watch time"),
        ("shares_per_1k", "shares per 1k views"),
        ("saves_per_1k", "saves per 1k views"),
    ]:
        values = [r.get(key) for r in rows if r.get(key) is not None]
        unit = {"skip": "%", "watch_through": "%", "avg_watch_s": "s"}.get(key, "")
        lines.append(f"- {label}: **{fmt(statistics.median(values), unit, 1) if values else '--'}** "
                     f"(range {fmt(min(values), unit, 1) if values else '--'} - {fmt(max(values), unit, 1) if values else '--'}, "
                     f"n={len(values)})")
    if not available["watch_through"] and not available["skip"]:
        lines.append("")
        lines.append("> Watch-through needs `enrich.py` (Reel duration) and skip rate needs a "
                     "current API version -- ranking is using shares/saves only.")

    lines.append("")
    lines.append("## Every Reel, best to worst")
    lines.append("")
    lines.append(table(rows))

    for title, subset in [("Winners", rows[: args.top]), ("Losers", rows[-args.top:][::-1])]:
        lines.append("")
        lines.append(f"## {title}")
        lines.append("")
        for row in subset:
            if row["score"] is None:
                continue
            bits = []
            if row["skip"] is not None:
                bits.append(f"skip {row['skip']:.0f}%")
            if row["watch_through"] is not None:
                bits.append(f"watch-through {row['watch_through']:.0f}%")
            if row["shares_per_1k"] is not None:
                bits.append(f"{row['shares_per_1k']:.1f} shares/1k")
            if row["saves_per_1k"] is not None:
                bits.append(f"{row['saves_per_1k']:.1f} saves/1k")
            lines.append(f"- **{row['score']:.0f}/100** {row['posted']} -- {', '.join(bits) or 'limited metrics'}")
            lines.append(f"  caption: {row['caption'][:100] or '(none)'}")
            if row["hook_text"]:
                lines.append(f"  first 3s: \"{row['hook_text'][:120]}\"")
            if row["permalink"]:
                lines.append(f"  {row['permalink']}")

    output = Path(__file__).resolve().parent / "data" / "report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n")

    json_path = output.parent / "reels.json"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))

    print("\n".join(lines))
    print(f"\nWrote {output}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
