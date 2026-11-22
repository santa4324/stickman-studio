#!/usr/bin/env python3
"""Draw the B1 cast and props for the StackShield explainer.

Fine black pen lines on transparency, drawn at 2x the 1280-wide design plane so
they stay crisp when the runtime scales them. Deterministic jitter keeps the
lines from looking like a vector icon set.
"""

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw

S = 2  # supersample factor against design units
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "stickman" / "art"
INK = (24, 26, 32, 255)
YELLOW = (244, 194, 74, 255)
RED = (219, 78, 62, 255)
GREEN = (58, 138, 96, 255)
BLUE = (63, 106, 168, 255)


def canvas(w, h):
    img = Image.new("RGBA", (int(w * S), int(h * S)), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def stroke(draw, points, width=7, colour=INK, jitter=1.1, seed=0):
    """Polyline with hand-drawn wobble: one continuous ink line, not dots."""
    rng = random.Random(seed)
    pts = [(x * S, y * S) for x, y in points]
    wobbled = []
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / 10))
        for i in range(steps):
            t = i / steps
            wobbled.append((
                x1 + (x2 - x1) * t + rng.uniform(-jitter, jitter) * S,
                y1 + (y2 - y1) * t + rng.uniform(-jitter, jitter) * S,
            ))
    wobbled.append(pts[-1])
    draw.line(wobbled, fill=colour, width=int(width * S), joint="curve")
    r = width * S / 2
    for x, y in (wobbled[0], wobbled[-1]):
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)


def path(draw, points, width=7, colour=INK, jitter=1.1, seed=0):
    stroke(draw, points, width, colour, jitter, seed)


def head(draw, cx, cy, r=46, look=0, mouth="flat", brows="flat", seed=1):
    """Round head, dot eyes, expressive brows and mouth."""
    draw.ellipse(
        [(cx - r) * S, (cy - r) * S, (cx + r) * S, (cy + r) * S],
        outline=INK, width=int(6 * S),
    )
    eye_dx = 15
    for side in (-1, 1):
        ex = cx + side * eye_dx + look
        if mouth == "wide":
            draw.ellipse([(ex - 6) * S, (cy - 14) * S, (ex + 6) * S, (cy - 2) * S], fill=INK)
        else:
            draw.ellipse([(ex - 5) * S, (cy - 10) * S, (ex + 5) * S, (cy) * S], fill=INK)

    # brows
    for side in (-1, 1):
        bx = cx + side * eye_dx
        if brows == "up":
            path(draw, [(bx - 12, cy - 24), (bx + 12, cy - 34)], 5, INK, 0.6, seed + side)
        elif brows == "pinch":
            path(draw, [(bx - 12, cy - 30), (bx + 12, cy - 22)], 5, INK, 0.6, seed + side)
        else:
            path(draw, [(bx - 12, cy - 26), (bx + 12, cy - 26)], 5, INK, 0.6, seed + side)

    if mouth == "wide":
        draw.ellipse([(cx - 13) * S, (cy + 10) * S, (cx + 13) * S, (cy + 34) * S], fill=INK)
    elif mouth == "grin":
        path(draw, [(cx - 16, cy + 18), (cx, cy + 30), (cx + 16, cy + 18)], 6, INK, 0.8, seed)
    elif mouth == "frown":
        path(draw, [(cx - 14, cy + 28), (cx, cy + 16), (cx + 14, cy + 28)], 6, INK, 0.8, seed)
    else:
        path(draw, [(cx - 12, cy + 22), (cx + 12, cy + 22)], 6, INK, 0.8, seed)


def standing(seed=3, arms="down", mouth="flat", brows="flat", lean=0):
    """A 4.4-heads-tall stick figure in a 320x520 design box."""
    img, d = canvas(320, 500)
    cx = 160 + lean
    head(d, cx, 82, 50, mouth=mouth, brows=brows, seed=seed)
    neck_y = 132
    hip_y = 286
    path(d, [(cx, neck_y), (cx + lean * 0.3, hip_y)], 9, INK, 1.2, seed)
    # legs, four-ish heads tall overall
    path(d, [(cx, hip_y), (cx - 30, 400), (cx - 42, 470)], 9, INK, 1.2, seed + 1)
    path(d, [(cx, hip_y), (cx + 30, 400), (cx + 42, 470)], 9, INK, 1.2, seed + 2)
    if arms == "up":
        path(d, [(cx, 158), (cx - 62, 96), (cx - 78, 40)], 8, INK, 1.2, seed + 3)
        path(d, [(cx, 158), (cx + 62, 96), (cx + 78, 40)], 8, INK, 1.2, seed + 4)
    elif arms == "point":
        path(d, [(cx, 158), (cx - 40, 220), (cx - 46, 268)], 8, INK, 1.2, seed + 3)
        path(d, [(cx, 158), (cx + 74, 150), (cx + 118, 128)], 8, INK, 1.2, seed + 4)
    elif arms == "hold":
        path(d, [(cx, 158), (cx - 40, 210), (cx - 20, 250)], 8, INK, 1.2, seed + 3)
        path(d, [(cx, 158), (cx + 40, 210), (cx + 22, 250)], 8, INK, 1.2, seed + 4)
    elif arms == "approve":
        path(d, [(cx, 158), (cx - 46, 214), (cx - 40, 264)], 8, INK, 1.2, seed + 3)
        path(d, [(cx, 158), (cx + 54, 126), (cx + 92, 104)], 8, INK, 1.2, seed + 4)
    else:
        path(d, [(cx, 158), (cx - 44, 226), (cx - 36, 286)], 8, INK, 1.2, seed + 3)
        path(d, [(cx, 158), (cx + 44, 226), (cx + 36, 286)], 8, INK, 1.2, seed + 4)
    return img


def seated_typing(seed=7):
    """Slumped-at-the-desk posture, hands on the keyboard."""
    img, d = canvas(420, 470)
    head(d, 150, 88, 50, mouth="flat", brows="pinch", seed=seed)
    path(d, [(150, 138), (126, 286)], 9, INK, 1.2, seed)          # torso, leaning in
    path(d, [(126, 286), (92, 380), (96, 440)], 9, INK, 1.2, seed + 1)
    path(d, [(126, 286), (168, 380), (164, 440)], 9, INK, 1.2, seed + 2)
    # both arms reach down-forward onto the keyboard
    path(d, [(140, 176), (206, 244), (268, 268)], 9, INK, 1.2, seed + 3)
    path(d, [(146, 194), (216, 262), (272, 282)], 9, INK, 1.2, seed + 4)
    return img


def laptop():
    img, d = canvas(300, 210)
    d.polygon([(40 * S, 40 * S), (260 * S, 40 * S), (272 * S, 152 * S), (28 * S, 152 * S)],
              fill=(250, 250, 247, 255), outline=INK, width=int(6 * S))
    d.polygon([(12 * S, 152 * S), (288 * S, 152 * S), (300 * S, 186 * S), (0, 186 * S)],
              fill=(238, 238, 232, 255), outline=INK, width=int(6 * S))
    # a red squiggle on the screen: the thing nobody looked at
    path(d, [(80, 76), (128, 96), (176, 70), (224, 100)], 6, RED, 1.0, 5)
    return img


def key_prop():
    img, d = canvas(220, 120)
    draw = ImageDraw.Draw(img)
    draw.ellipse([18 * S, 22 * S, 98 * S, 102 * S], outline=INK, width=int(7 * S))
    draw.ellipse([44 * S, 48 * S, 72 * S, 76 * S], fill=(0, 0, 0, 0), outline=INK, width=int(5 * S))
    path(d, [(96, 62), (196, 62)], 9, INK, 0.8, 2)
    path(d, [(160, 62), (160, 92)], 8, INK, 0.8, 3)
    path(d, [(184, 62), (184, 86)], 8, INK, 0.8, 4)
    return img


def database():
    img, d = canvas(260, 300)
    for i, y in enumerate((40, 110, 180)):
        d.ellipse([30 * S, y * S, 230 * S, (y + 60) * S], outline=INK, width=int(6 * S),
                  fill=(250, 250, 247, 255) if i == 0 else None)
    path(d, [(30, 70), (30, 210)], 6, INK, 0.8, 1)
    path(d, [(230, 70), (230, 210)], 6, INK, 0.8, 2)
    path(d, [(60, 158), (110, 176), (160, 152), (198, 172)], 6, RED, 0.8, 3)
    return img


def padlock(open_=True):
    img, d = canvas(200, 220)
    d.rounded_rectangle([36 * S, 96 * S, 164 * S, 200 * S], radius=12 * S,
                        fill=(250, 250, 247, 255), outline=INK, width=int(7 * S))
    if open_:
        path(d, [(60, 96), (60, 60), (96, 40), (132, 58), (132, 74)], 10, INK, 0.8, 1)
    else:
        path(d, [(60, 96), (60, 52), (100, 30), (140, 52), (140, 96)], 10, INK, 0.8, 2)
    d.ellipse([88 * S, 132 * S, 112 * S, 156 * S], fill=INK)
    return img


def checkmark():
    img, d = canvas(200, 200)
    d.ellipse([10 * S, 10 * S, 190 * S, 190 * S], outline=GREEN, width=int(10 * S))
    path(d, [(56, 104), (88, 138), (146, 66)], 14, GREEN, 0.8, 1)
    return img


def warning():
    img, d = canvas(220, 200)
    d.polygon([(110 * S, 16 * S), (206 * S, 180 * S), (14 * S, 180 * S)],
              fill=(252, 246, 232, 255), outline=RED, width=int(8 * S))
    path(d, [(110, 74), (110, 126)], 12, RED, 0.6, 1)
    d.ellipse([102 * S, 142 * S, 118 * S, 158 * S], fill=RED)
    return img


def counter():
    """Ten tables, nine of them open: the statistic, drawn."""
    img, d = canvas(980, 340)
    box, gap = 160, 30
    for i in range(10):
        col, row = i % 5, i // 5
        x = 20 + col * (box + gap)
        y = 30 + row * (box + gap)
        filled = i != 9
        d.rounded_rectangle(
            [x * S, y * S, (x + box) * S, (y + box) * S], radius=18 * S,
            fill=(RED[0], RED[1], RED[2], 58) if filled else None,
            outline=RED if filled else INK, width=int(8 * S),
        )
        if filled:
            path(d, [(x + 42, y + 82), (x + 72, y + 112), (x + 120, y + 48)],
                 10, RED, 0.6, i)
    return img


def repo_card():
    """A generic repository card: rounded frame, branch mark, file rows."""
    img, d = canvas(680, 600)
    d.rounded_rectangle([20 * S, 20 * S, 660 * S, 580 * S], radius=24 * S,
                        fill=(250, 250, 247, 255), outline=INK, width=int(7 * S))
    # branch glyph
    path(d, [(90, 90), (90, 170)], 8, INK, 1.0, 1)
    d.ellipse([74 * S, 66 * S, 106 * S, 98 * S], outline=INK, width=int(5 * S))
    d.ellipse([74 * S, 158 * S, 106 * S, 190 * S], outline=INK, width=int(5 * S))
    path(d, [(100, 120), (150, 120), (150, 174)], 8, INK, 1.0, 2)
    d.ellipse([134 * S, 162 * S, 166 * S, 194 * S], outline=INK, width=int(5 * S))
    for i, width in enumerate((320, 420, 260, 380)):
        y = 300 + i * 62
        path(d, [(80, y), (80 + width, y)], 9, INK, 0.9, i + 10)
    return img


def main(out_dir=None):
    OUT = Path(out_dir) if out_dir else DEFAULT_OUT
    OUT.mkdir(parents=True, exist_ok=True)
    assets = {
        "char-typing.png": seated_typing(),
        "char-shock.png": standing(arms="up", mouth="wide", brows="up", lean=-6),
        "char-point.png": standing(seed=11, arms="point", mouth="grin", brows="up"),
        "char-hold.png": standing(seed=21, arms="hold", mouth="flat"),
        "char-approve.png": standing(seed=31, arms="approve", mouth="grin"),
        "char-slump.png": standing(seed=41, arms="down", mouth="frown", brows="pinch", lean=8),
        "laptop.png": laptop(),
        "key.png": key_prop(),
        "db.png": database(),
        "lock-open.png": padlock(True),
        "lock-closed.png": padlock(False),
        "check.png": checkmark(),
        "warning.png": warning(),
        "counter.png": counter(),
        "repo.png": repo_card(),
    }
    for name, img in assets.items():
        img.save(OUT / name)
        print(name, img.size)


if __name__ == "__main__":
    main()
