"""
One-off asset pipeline: turn the raw logo PNGs (opaque black background,
lots of empty margin) into the trimmed, transparent app assets + a Windows .ico.

The raw sources (several MB each) are NOT in the repository: keep them in the
git-ignored `branding/` folder next to this project. Only the small results
(mephistocr/assets/mark.png, app.ico) are versioned.

Run:  python tools/prepare_assets.py      (needs Pillow, see requirements-dev.txt)
"""
import os

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRANDING = os.path.join(ROOT, "branding")
ASSETS = os.path.join(ROOT, "mephistocr", "assets")

SOURCES = {
    "mark": "MephistOcr_logo1.png",      # the demon/document emblem
    "wordmark": "MephistOcr_logo2.png",  # the "MephistOCR" lettering (README banner only)
}

MARK_PX = 128      # the app shows the mark at 20-28 px; 128 covers 200 % scaling
BLACK_CUTOFF = 26  # pixels darker than this on all channels are background


def key_out_black(im: Image.Image, cutoff: int = BLACK_CUTOFF) -> Image.Image:
    """Make the near-black backdrop transparent, with a soft ramp so the
    glow edges don't get a hard halo."""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            lum = max(r, g, b)
            if lum <= cutoff:
                px[x, y] = (r, g, b, 0)
            elif lum < cutoff * 3:
                # soft ramp: fade in alpha over the dark-but-not-black band
                ratio = (lum - cutoff) / float(cutoff * 2)
                px[x, y] = (r, g, b, int(a * ratio))
    return im


def trimmed(src_name: str) -> Image.Image:
    src = os.path.join(BRANDING, src_name)
    if not os.path.exists(src):
        raise SystemExit(f"missing source image: {src} (raw logos live in branding/)")
    im = key_out_black(Image.open(src))
    bbox = im.getbbox()
    return im.crop(bbox) if bbox else im


def main():
    mark = trimmed(SOURCES["mark"])
    trimmed(SOURCES["wordmark"]).save(os.path.join(BRANDING, "wordmark.png"))

    small = mark.copy()
    small.thumbnail((MARK_PX, MARK_PX), Image.LANCZOS)
    small.save(os.path.join(ASSETS, "mark.png"), optimize=True)

    # square-pad the full-resolution mark, then emit a multi-resolution .ico
    # for the taskbar / alt-tab / window icon
    w, h = mark.size
    side = max(w, h)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(mark, ((side - w) // 2, (side - h) // 2), mark)
    square.save(os.path.join(ASSETS, "app.ico"), format="ICO",
                sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("assets written to", ASSETS)


if __name__ == "__main__":
    main()
