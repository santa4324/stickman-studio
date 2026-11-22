#!/usr/bin/env python3
"""Write the words that come with a Reel: on-screen ask, caption, first comment.

The overlay hook stops the scroll. What converts a view into a comment, a save
or a send is the ask -- and those three are the signals the ranking system reads
when it decides whether to push the Reel to strangers. Views alone are what a
Reel gets; interactions are what make it keep getting shown.

So every render gets a pack, four pieces:

    cta            one short line drawn at the bottom of the frame
    caption        the text under the Reel in the feed
    first_comment  the first thing you post under your own Reel, a reply bait
    hashtags       five tags, rotated per hook so posts don't look copy-pasted

No model, no API, no key. The copy is a small curated bank picked by a hash of
the hook, so the same hook always produces the same pack: reproducible, and easy
to A/B against data/renders.csv once the numbers come back.

    python3 caption.py --hook "me checking for leaked secrets before launch"
    python3 caption.py --cta                       # just one on-screen ask
    python3 caption.py --variants hooks.txt --out-dir out   # a .txt pack per hook
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Asking for a comment builds conversation; asking for a save builds intent;
# asking for a send builds reach. All three beat "like this post".
COMMENT = {
    "questions": [
        "be honest -- did you check this before launch, or after it?",
        "what's the first thing you'd check on your own app? wrong answers welcome.",
        "if you're mid-build right now: what stack are you shipping on?",
        "genuine question -- how many of your last five builds had row level security on?",
        "which one is worse: no auth, or auth with every row readable?",
    ],
    "ctas": [
        'comment "audit" and I\'ll send the four things I check first',
        "comment your stack and I'll test yours next",
        'reply "me" if this is you right now',
        "tell me what you'd have shipped without checking",
    ],
}

SAVE = {
    "questions": [
        "you won't remember this at 1am. that's what the save button is for.",
        "saving this for whoever ships next weekend without reading it.",
        "keep it. the check is four minutes, the cleanup is six weeks.",
    ],
    "ctas": [
        "save this for launch week",
        "save it now, thank yourself the night before you ship",
        "save it, then actually run the check",
    ],
}

SHARE = {
    "questions": [
        "someone in your group chat is one REST call away from this.",
        "send this to the friend who said security comes after launch.",
        "you know exactly who needs to see this.",
    ],
    "ctas": [
        "send this to the person you're building with",
        "tag the cofounder who said security can wait",
        "share this with whoever owns your database",
    ],
}

INTENTS = {"comment": COMMENT, "save": SAVE, "share": SHARE}
INTENT_ORDER = ["comment", "save", "share"]

FIRST_COMMENTS = [
    "the numbers here are from apps I checked this week, with permission -- not a demo.",
    "yes, anon key in the bundle plus RLS off is the whole bug. yes, it's that common.",
    "if you want the four checks, they're in my pinned post. no signup, no scan required.",
    "the awkward part: none of these people were careless. the defaults are just quiet.",
    "I'll test the next five apps people drop in the comments, in order.",
]

PRIVACY_TIP = (
    "free source: General Legal publishes attorney-drafted privacy policies "
    "(US and GDPR) under CC0 -- fill in the highlighted fields, keep their credit."
)

# Tip hooks have to actually hand over the tip, or the Reel is just a tease.
TIP_COMMENTS = [
    "no tool needed: devtools, network tab, reload, then read every request that isn't yours.",
    "the free version is four checks: RLS on every table, no sk_ keys in the bundle, nothing secret in git history, CORS not *.",
    "if you only do one: supabase -> table editor -> check every table has RLS on. tables ship with it off.",
    "privacy policy shortcut: list the tools you actually use, link their policies, date it. a template is fine.",
    "revoking a leaked key takes 40 seconds: provider -> API keys -> revoke -> new key -> env var -> redeploy.",
]

HASHTAG_SETS = [
    ["buildinpublic", "indiehackers", "supabase", "appsecurity", "vibecoding"],
    ["buildinpublic", "startup", "cybersecurity", "saas", "launchday"],
    ["indiehackers", "100daysofcode", "webdev", "security", "founderlife"],
    ["supabase", "nextjs", "appsecurity", "buildinpublic", "techdebt"],
]


def pick(options, seed):
    """Stable choice: the same hook always gets the same pack."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return options[digest[0] % len(options)]


def intent_for(hook):
    return pick(INTENT_ORDER, "intent:" + hook)


def normalize_hook(hook):
    return " ".join(hook.split()).rstrip(".")


def intent_for_cta(cta):
    """Which bank an ask came from -- a batch rotates them, a hook does not."""
    for name, bank in INTENTS.items():
        if cta in bank["ctas"]:
            return name
    return None


def build_pack(hook, cta=None, caption_body=None, first_comment=None):
    """Everything that goes around one render, as plain values.

    caption_body and first_comment let one Reel carry its own sourced detail
    instead of the bank's generic line -- a study number, a link, a caveat.
    """
    hook = normalize_hook(hook)
    intent = (intent_for_cta(cta) if cta else None) or intent_for(hook)
    bank = INTENTS[intent]
    cta = cta or pick(bank["ctas"], "cta:" + hook)
    question = caption_body or pick(bank["questions"], "question:" + hook)
    if first_comment:
        comment = first_comment
    elif "privacy polic" in hook.lower():
        comment = PRIVACY_TIP
    else:
        pool = TIP_COMMENTS if hook.lower().startswith("free") else FIRST_COMMENTS
        comment = pick(pool, "comment:" + hook)
    tags = list(pick(HASHTAG_SETS, "tags:" + hook))

    caption = "\n\n".join([
        hook,
        question,
        cta,
        " ".join("#" + tag for tag in tags),
    ])
    return {
        "hook": hook,
        "intent": intent,
        "cta": cta,
        "caption": caption,
        "first_comment": comment,
        "hashtags": tags,
    }


def format_pack(pack):
    """One file per render: paste from here, nothing to assemble by hand."""
    return "\n".join([
        f"ON-SCREEN HOOK : {pack['hook']}",
        f"ON-SCREEN CTA  : {pack['cta']}",
        f"ASK TYPE       : {pack['intent']}",
        "",
        "--- caption (paste into Instagram) ----------------------------",
        pack["caption"],
        "",
        "--- first comment (post it yourself, then pin it) -------------",
        pack["first_comment"],
        "",
        "--- alt text (accessibility, also read by the feed) -----------",
        f"{pack['hook']}. Short looping clip with a small caption near the top.",
        "",
    ])


def write_pack(path, hook, cta=None, caption_body=None, first_comment=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_pack(build_pack(hook, cta, caption_body, first_comment)))
    return path


def random_cta(hook="", index=None):
    """The on-screen ask for a hook.

    Pass the render's position in a batch and the ask type rotates instead of
    hashing, so a batch of three tests comment, save and share rather than
    asking for the same thing three times.
    """
    hook = normalize_hook(hook)
    intent = INTENT_ORDER[index % len(INTENT_ORDER)] if index is not None else intent_for(hook)
    return pick(INTENTS[intent]["ctas"], f"cta:{intent}:{hook}")


def slugify(text, limit=6):
    words = re.sub(r"[^a-z0-9\s-]", "", text.lower()).split()
    return ("-".join(words[:limit]) or "reel")[:60]


def load_hooks(path):
    hooks = []
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            hooks.append(line)
    if not hooks:
        raise SystemExit(f"{path} has no hook lines (one per line, # for comments).")
    return hooks


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--hook", help="the on-screen line, quotes and all")
    parser.add_argument("--variants", help="file of hooks; writes one .txt pack each")
    parser.add_argument(
        "--out-dir", default=str(ROOT / "out"), help="where --variants writes packs"
    )
    parser.add_argument("--cta", action="store_true", help="print one on-screen ask and exit")
    parser.add_argument("--write", help="also save the single --hook pack here")
    args = parser.parse_args()

    if args.cta and not args.hook:
        print(random_cta())
        return

    if args.variants:
        out_dir = Path(args.out_dir)
        hooks = load_hooks(args.variants)
        for index, hook in enumerate(hooks, 1):
            path = out_dir / f"{index:02d}-{slugify(hook)}.txt"
            write_pack(path, hook)
            print(f"[{index}/{len(hooks)}] {path}")
        return

    if not args.hook:
        raise SystemExit("Pass --hook \"the on-screen line\", or --variants hooks.txt, or --cta.")

    pack = build_pack(args.hook, random_cta(args.hook))
    print(format_pack(pack))
    if args.write:
        path = write_pack(args.write, args.hook)
        print(f"Wrote {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
