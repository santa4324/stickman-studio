#!/usr/bin/env python3
"""Turn data/reels.json into "what is working", not just "what scored well".

report.py ranks individual Reels. This groups them into cohorts -- caption shape,
duration band, whether there is an ask -- because the useful question is not
"which Reel won" but "what do my winners have in common that my losers don't".

It also joins data/renders.csv to the synced Reels: each pack from caption.py
starts the caption with the on-screen hook, so a Reel that used one of your
renders can be matched back to the exact line and ask that produced it. No
matches is normal until you post something you rendered here.

    python3 learn.py                 # write data/learnings.md + print it
    python3 learn.py --min-n 2       # show cohorts with at least 2 Reels

Every cohort prints its n. A median over 3 Reels is a hint, not a finding --
this is deliberately blunt about which rows are thin.
"""

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
REELS = DATA / "reels.json"
RENDERS = DATA / "renders.csv"
OUTPUT = DATA / "learnings.md"

# Captions that open by selling. Everything your account posts currently starts
# this way, so it is worth measuring separately from the story-led ones.
PRODUCT_OPENERS = (
    "try for free",
    "try it for free",
    "get your free",
    "scan your stack",
    "bring your own",
    "stackshield",
    "want the full guide",
    "comment \"funnel\"",
    "create unlimited",
    "🚀 create unlimited",
)

ASK_PATTERN = re.compile(
    r"\b(comment|save this|save it|tag |send this|share this|which one|would you|"
    r"have you|did you|what would|what's the|who else)\b|\?",
    re.IGNORECASE,
)


def load_reels():
    if not REELS.exists():
        raise SystemExit("No data/reels.json yet. Run: python3 sync.py && python3 report.py")
    return json.loads(REELS.read_text())


def normalize(text):
    return " ".join((text or "").lower().split())


def caption_shape(reel):
    caption = normalize(reel.get("caption"))
    return "product-first" if caption.startswith(PRODUCT_OPENERS) else "story-first"


def ask_shape(reel):
    return "has an ask" if ASK_PATTERN.search(reel.get("caption") or "") else "no ask"


def duration_band(reel):
    """Few bands, so each one can fill: short is the format being tested."""
    duration = reel.get("duration_s")
    if not duration:
        return "duration unknown"
    if duration <= 10:
        return "<=10s"
    if duration <= 16:
        return "10-16s"
    return "16s+"


def per_k(reel, key):
    views = reel.get("views")
    value = reel.get(key)
    if value is None or not views:
        return None
    return value / views * 1000


def median(values):
    usable = [v for v in values if v is not None]
    return statistics.median(usable) if usable else None


def cohort_stats(reels):
    return {
        "n": len(reels),
        "views": median([r.get("views") for r in reels]),
        "skip": median([r.get("skip") for r in reels]),
        "through": median([r.get("watch_through") for r in reels]),
        "saves": median([per_k(r, "saves") for r in reels]),
        "shares": median([per_k(r, "shares") for r in reels]),
        "comments": median([per_k(r, "comments") for r in reels]),
    }


def fmt(value, digits=0, suffix=""):
    return "--" if value is None else f"{value:,.{digits}f}{suffix}"


def cohort_table(title, groups, min_n):
    lines = [
        f"### {title}",
        "",
        "| cohort | n | views | skip | watch-through | saves/1k | shares/1k | comments/1k |",
        "|---|---|---|---|---|---|---|---|",
    ]
    thin = []
    for name, reels in groups:
        stats = cohort_stats(reels)
        lines.append(
            f"| {name} | {stats['n']} | {fmt(stats['views'])} | {fmt(stats['skip'], 1, '%')} | "
            f"{fmt(stats['through'], 1, '%')} | {fmt(stats['saves'], 1)} | "
            f"{fmt(stats['shares'], 1)} | {fmt(stats['comments'], 2)} |"
        )
        if stats["n"] < min_n:
            thin.append(name)
    lines.append("")
    if thin:
        lines.append(f"Thin sample (n<{min_n}), treat as a hint: {', '.join(thin)}.")
        lines.append("")
    return lines


def existing_captions(reels):
    return [(r, normalize(r.get("caption"))) for r in reels]


def join_renders(reels, min_overlap=18):
    """Match renders.csv hooks to synced Reels by the start of their caption.

    caption.py writes the hook as the first caption line, so a Reel posted from
    a render starts with that exact text. Shows which line earned what.
    """
    if not RENDERS.exists():
        return []
    with RENDERS.open(newline="") as handle:
        renders = list(csv.DictReader(handle))
    captions = existing_captions(reels)
    matches = []
    for render in renders:
        hook = normalize(render.get("hook"))
        if len(hook) < min_overlap:
            continue
        for reel, caption in captions:
            if caption.startswith(hook):
                matches.append((render, reel))
                break
    return matches


def best_and_worst(reels, limit=3):
    scored = [r for r in reels if r.get("score") is not None]
    scored.sort(key=lambda r: -r["score"])
    return scored[:limit], scored[-limit:][::-1]


def describe(reel):
    bits = [
        reel.get("posted") or "--",
        f"{fmt(reel.get('views'))} views",
        f"skip {fmt(reel.get('skip'), 1, '%')}",
        f"{fmt(reel.get('saves'), 0)} saves / {fmt(reel.get('shares'), 0)} shares",
    ]
    return f"score {fmt(reel.get('score'), 1)} -- " + ", ".join(bits)


def totals(reels, key):
    return sum(r.get(key) or 0 for r in reels)


def verdicts(reels, groups, min_n):
    """Sentences that follow from the tables, with the sample size attached.

    Guards matter more than the prose here. A per-1k ratio over a Reel with 20
    views can make one comment look like twenty-seven, and two medians 1 point
    apart are noise. Nothing is called a winner unless the gap is bigger than
    the noise floor, and interaction is compared in absolute counts first.
    """
    out = []
    all_comments = totals(reels, "comments")
    all_saves = totals(reels, "saves")
    all_shares = totals(reels, "shares")
    if all_comments + all_saves + all_shares < 10:
        out.append(
            f"Across {len(reels)} Reels there is almost no interaction to compare: "
            f"{all_comments:.0f} comments, {all_saves:.0f} saves, {all_shares:.0f} shares. "
            "Nothing is being asked for, so the ranking has no reason to push these further."
        )

    caption_groups = dict(groups["caption shape"])
    if all(len(caption_groups.get(k, [])) >= min_n for k in ("story-first", "product-first")):
        story_reels = caption_groups["story-first"]
        product_reels = caption_groups["product-first"]
        story = cohort_stats(story_reels)
        product = cohort_stats(product_reels)

        if story["skip"] is not None and product["skip"] is not None:
            gap = abs(story["skip"] - product["skip"])
            if gap >= 3:
                winner = "story-first" if story["skip"] < product["skip"] else "product-first"
                out.append(
                    f"{winner} captions hold the first 3 seconds better: median skip "
                    f"{fmt(min(story['skip'], product['skip']), 1, '%')} vs "
                    f"{fmt(max(story['skip'], product['skip']), 1, '%')} "
                    f"(gap {fmt(gap, 1)} points, n={story['n']}+{product['n']})."
                )
            else:
                out.append(
                    f"Caption shape makes no measurable difference to skip rate: "
                    f"{fmt(story['skip'], 1, '%')} vs {fmt(product['skip'], 1, '%')} "
                    f"(gap under 3 points). The problem is inside the first 3 seconds, "
                    "not in the caption."
                )

        for key, label in (("comments", "comments"), ("saves", "saves"), ("shares", "shares")):
            story_total, product_total = totals(story_reels, key), totals(product_reels, key)
            if max(story_total, product_total) < 3 or story_total == product_total:
                continue
            winner = "story-first" if story_total > product_total else "product-first"
            out.append(
                f"{winner} captions earned {label}: {max(story_total, product_total):.0f} "
                f"vs {min(story_total, product_total):.0f} in total. Small counts -- "
                "re-check after ten more posts."
            )

    for name, group in groups.get("duration", []):
        stats = cohort_stats(group)
        if name != "duration unknown" and stats["n"] >= min_n and stats["through"] is not None:
            out.append(
                f"{name} is the only band with enough Reels to read (n={stats['n']}): "
                f"{fmt(stats['through'], 1, '%')} watch-through, skip "
                f"{fmt(stats['skip'], 1, '%')}. Shorter bands are under-sampled, so "
                "the next posts should test them deliberately."
            )
            break

    thin = [name for name, group in groups["caption shape"] if len(group) < min_n]
    if thin:
        out.append(f"Small samples, read with care: {', '.join(thin)}.")
    return out


def build(reels, min_n):
    lines = ["# What is working", ""]
    lines.append(
        f"{len(reels)} Reels synced. Medians are within your own account, so they "
        "describe your baseline rather than Instagram's."
    )
    lines.append("")

    groups = {
        "caption shape": sorted(
            ((name, [r for r in reels if caption_shape(r) == name]) for name in ("story-first", "product-first")),
            key=lambda pair: -len(pair[1]),
        ),
        "ask": [(name, [r for r in reels if ask_shape(r) == name]) for name in ("has an ask", "no ask")],
        "duration": [
            (name, [r for r in reels if duration_band(r) == name])
            for name in ("<=10s", "10-16s", "16s+", "duration unknown")
        ],
    }

    for title, key in (("Caption shape", "caption shape"), ("Ask in the caption", "ask"), ("Duration", "duration")):
        lines += cohort_table(title, groups[key], min_n)

    best, worst = best_and_worst(reels)
    lines.append("### Best three")
    lines.append("")
    for reel in best:
        lines.append(f"- {describe(reel)}")
        lines.append(f"  `{(reel.get('caption') or '')[:120]}`")
    lines.append("")
    lines.append("### Worst three")
    lines.append("")
    for reel in worst:
        lines.append(f"- {describe(reel)}")
        lines.append(f"  `{(reel.get('caption') or '')[:120]}`")
    lines.append("")

    matches = join_renders(reels)
    lines.append("### Renders that got posted")
    lines.append("")
    if matches:
        lines.append("| hook | ask type | posted | views | skip | saves | shares |")
        lines.append("|---|---|---|---|---|---|---|")
        for render, reel in matches:
            lines.append(
                f"| {render.get('hook', '')[:60]} | {render.get('clip', '')} | {reel.get('posted')} | "
                f"{fmt(reel.get('views'))} | {fmt(reel.get('skip'), 1, '%')} | "
                f"{fmt(reel.get('saves'))} | {fmt(reel.get('shares'))} |"
            )
    else:
        lines.append(
            "None yet. The join matches a Reel's caption to the hook in "
            "renders.csv, so it fills in the first time you post something "
            "rendered here with its caption pasted from the pack."
        )
    lines.append("")

    lines.append("### What follows")
    lines.append("")
    for sentence in verdicts(reels, groups, min_n) or ["Not enough data for a verdict yet."]:
        lines.append(f"- {sentence}")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--min-n", type=int, default=3, help="cohort size below this is called thin")
    args = parser.parse_args()

    reels = load_reels()
    report = build(reels, args.min_n)
    OUTPUT.write_text(report)
    print(report)
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
