"""Generate the illustrated doorbell frames used by the offline demo.

These are drawings, not photos, and each is labelled as such. Each has a sidecar JSON with
the result the offline FixtureVision returns. For the recorded demo, replace them with
photos you took yourself (same file names) and run with BEDROCK_ENABLED=1 so the counts
come from the model, not from the sidecar files.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "demo" / "images"
W, H = 960, 720
FONT = "/usr/share/fonts/opentype/inter/Inter-Medium.otf"


def font(size: int):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def fisheye(img: Image.Image, k: float = 0.22) -> Image.Image:
    """Barrel distortion, the look of a wide-angle doorbell lens."""
    src = img.load()
    out = Image.new("RGB", img.size)
    dst = out.load()
    cx, cy = W / 2, H / 2
    rmax = math.hypot(cx, cy)
    for y in range(H):
        for x in range(W):
            dx, dy = (x - cx) / rmax, (y - cy) / rmax
            r = math.hypot(dx, dy)
            f = 1 + k * r * r
            sx, sy = int(cx + dx * rmax / f), int(cy + dy * rmax / f)
            if 0 <= sx < W and 0 <= sy < H:
                dst[x, y] = src[sx, sy]
    return out


def porch(night: bool) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    sky = (18, 22, 30) if night else (170, 196, 214)
    img = Image.new("RGB", (W, H), sky)
    d = ImageDraw.Draw(img)
    ground = (40, 38, 36) if night else (128, 120, 108)
    d.rectangle([0, int(H * 0.62), W, H], fill=ground)
    # path / steps
    d.polygon([(W * 0.38, H), (W * 0.62, H), (W * 0.56, H * 0.62), (W * 0.44, H * 0.62)], fill=(70, 66, 60) if night else (176, 168, 156))
    # hedge
    hedge = (22, 40, 28) if night else (64, 108, 70)
    for i in range(14):
        x = i * 75 - 20
        d.ellipse([x, H * 0.48, x + 110, H * 0.7], fill=hedge)
    if night:
        glow = Image.new("L", (W, H), 0)
        g = ImageDraw.Draw(glow)
        g.ellipse([W * 0.25, H * 0.35, W * 0.75, H * 1.1], fill=110)
        glow = glow.filter(ImageFilter.GaussianBlur(90))
        warm = Image.new("RGB", (W, H), (255, 214, 150))
        img = Image.composite(warm, img, glow)
        d = ImageDraw.Draw(img)
    return img, d


def person(d: ImageDraw.ImageDraw, x: float, y: float, s: float, night: bool, rnd: random.Random) -> None:
    tone = rnd.choice([(30, 30, 34), (52, 48, 56), (44, 52, 60), (60, 50, 44)]) if night else rnd.choice([(60, 70, 90), (120, 60, 50), (40, 90, 80)])
    head = 22 * s
    d.ellipse([x - head, y - 170 * s, x + head, y - 170 * s + 2 * head], fill=tone)
    d.rounded_rectangle([x - 34 * s, y - 128 * s, x + 34 * s, y - 20 * s], radius=int(18 * s), fill=tone)
    d.rectangle([x - 26 * s, y - 30 * s, x - 6 * s, y + 40 * s], fill=tone)
    d.rectangle([x + 6 * s, y - 30 * s, x + 26 * s, y + 40 * s], fill=tone)


def label(img: Image.Image, stamp: str) -> Image.Image:
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16, H - 52, 470, H - 16], radius=8, fill=(0, 0, 0))
    d.text((28, H - 46), "ILLUSTRATION · replace with your own photo", font=font(20), fill=(255, 255, 255))
    d.text((W - 230, 18), stamp, font=font(22), fill=(235, 235, 235))
    return img


def save(name: str, img: Image.Image, fixture: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    img.save(OUT / f"{name}.jpg", quality=85)
    (OUT / f"{name}.json").write_text(json.dumps(fixture, indent=2) + "\n")


def main() -> None:
    rnd = random.Random(7)

    img, d = porch(night=True)
    for i, (x, y, s) in enumerate([(300, 640, 1.0), (390, 610, 0.9), (470, 650, 1.05), (560, 620, 0.92), (650, 655, 1.0), (430, 560, 0.75), (520, 555, 0.72), (610, 565, 0.74), (350, 560, 0.7)]):
        person(d, x, y, s, True, rnd)
    save("harbor-gathering", label(fisheye(img), "23:15"), {
        "people_count": 9, "vehicle_present": False, "package_present": False, "confidence": 0.82,
        "scene": "group standing at front door at night",
    })

    img, d = porch(night=True)
    person(d, 500, 640, 1.1, True, rnd)
    save("cedar-night", label(fisheye(img), "21:42"), {
        "people_count": 1, "vehicle_present": False, "package_present": False, "confidence": 0.77,
        "scene": "one person on the front path at night",
    })

    img, d = porch(night=False)
    for i in range(6):
        x = 640 + i * 18
        d.line([(x, H * 0.62), (x + 40, H * 0.38)], fill=(70, 110, 60), width=5)
    save("cedar-wind", label(fisheye(img), "09:40"), {
        "people_count": 0, "vehicle_present": False, "package_present": False, "confidence": 0.9,
        "scene": "empty front path, plants moving",
    })
    print("wrote", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
