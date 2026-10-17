"""Draw the JunkZero app icon: the header's logo (a white shield with a check mark on the
blue-to-indigo badge) as JunkZero.png, JunkZero.ico (Windows) and JunkZero.icns (macOS).

The icons are committed; run this again only to change them:
    python packaging/make_icon.py
Needs Pillow (pip install pillow); the app itself does not.
"""
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
SIZE = 1024
TOP_LEFT = (0x3B, 0x82, 0xF6)      # same gradient as .logo-badge in app/ui/styles.css
BOTTOM_RIGHT = (0x63, 0x66, 0xF1)


def _gradient(size: int) -> Image.Image:
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(TOP_LEFT, BOTTOM_RIGHT))
    return img


def draw(size: int = SIZE) -> Image.Image:
    s = size / 1024
    badge = _gradient(size)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(224 * s), fill=255)
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(badge, (0, 0), mask)

    d = ImageDraw.Draw(icon)
    white = (255, 255, 255, 255)
    width = int(64 * s)
    # Shield outline (as in the lucide "shield-check" icon)
    shield = [(512, 196), (782, 296), (782, 520), (760, 620), (700, 712), (512, 836),
              (324, 712), (264, 620), (242, 520), (242, 296)]
    shield = [(x * s, y * s) for x, y in shield]
    d.line(shield + [shield[0]], fill=white, width=width, joint="curve")
    for x, y in shield:
        r = width / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=white)
    # Check mark
    check = [(392 * s, 520 * s), (476 * s, 604 * s), (640 * s, 440 * s)]
    d.line(check, fill=white, width=width, joint="curve")
    for x, y in (check[0], check[-1]):
        r = width / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=white)
    return icon


def main() -> None:
    icon = draw()
    icon.save(os.path.join(HERE, "JunkZero.png"))
    icon.save(os.path.join(HERE, "JunkZero.ico"),
              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(os.path.join(HERE, "JunkZero.icns"))
    print("Wrote JunkZero.png, JunkZero.ico and JunkZero.icns in", HERE)


if __name__ == "__main__":
    main()
