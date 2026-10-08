#!/usr/bin/env python3
"""Generate Home Assistant brand assets for the `canon_printer` integration.

Flat, modern front-view illustration of a colour laser printer:
rounded body, output tray with a sheet sticking out, a small control
panel with an LED, and a paper cassette at the bottom.

Outputs (into custom_components/canon_printer/brand/):
    icon.png       256x256   icon@2x.png      512x512
    dark_icon.png  256x256   dark_icon@2x.png 512x512
    logo.png       1024x256  logo@2x.png      2048x512
    dark_logo.png  1024x256  dark_logo@2x.png 2048x512

The artwork is drawn at 4x and downscaled with LANCZOS for clean edges.
Run:  python tools/generate_brand_assets.py
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
BRAND_DIR = os.path.join(REPO, "custom_components", "canon_printer", "brand")

# Supersampling factor: draw big, shrink with LANCZOS.
SS = 4

FONT_CANDIDATES = [
    os.path.join(HERE, "DejaVuSans-Bold.ttf"),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]

# --- Palettes -------------------------------------------------------------
# Light theme: dark slate body, HA blue accent, white sheet.
LIGHT = {
    "body": (0x2C, 0x3E, 0x50),
    "cassette": (0x1E, 0x2C, 0x3C),
    "slot": (0x11, 0x1B, 0x27),
    "panel": (0x3A, 0x50, 0x6A),
    "sheet": (0xFF, 0xFF, 0xFF),
    "sheet_line": (0xC2, 0xCC, 0xD4),
    "accent": (0x03, 0xA9, 0xF4),
    "text": (0x2C, 0x3E, 0x50),
}

# Dark theme: light body so it stays readable on dark backgrounds.
DARK = {
    "body": (0xE8, 0xEA, 0xED),
    "cassette": (0xCD, 0xD1, 0xD7),
    "slot": (0xA6, 0xAC, 0xB4),
    "panel": (0xF5, 0xF6, 0xF8),
    "sheet": (0xFF, 0xFF, 0xFF),
    "sheet_line": (0xB4, 0xBC, 0xC6),
    "accent": (0x03, 0xA9, 0xF4),
    "text": (0xE8, 0xEA, 0xED),
}


def _rr(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def _paper_layer(size, pal, box):
    """Sheet of paper emerging from the output tray, slightly tilted."""
    x0, y0, x1, y1 = box
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ox, oy, s = box[0], box[1], (x1 - x0)
    # sheet geometry (fractions of the square drawing area)
    px0 = ox + 0.30 * s
    py0 = oy + 0.05 * s
    px1 = ox + 0.66 * s
    py1 = oy + 0.46 * s
    r = 0.018 * s
    _rr(d, (px0, py0, px1, py1), r, pal["sheet"])
    # two faint text lines to read as a printout
    lx0 = ox + 0.345 * s
    lx1 = ox + 0.615 * s
    lw = max(1, int(0.012 * s))
    for ly_frac in (0.11, 0.16):
        ly = oy + ly_frac * s
        d.rounded_rectangle((lx0, ly, lx1, ly + lw), radius=lw / 2,
                            fill=pal["sheet_line"])
    layer = layer.rotate(-7, resample=Image.Resampling.BICUBIC, center=(ox + 0.48 * s,
                                                             oy + 0.25 * s))
    return layer


def draw_printer(canvas, box, pal):
    """Draw the printer pictogram inside a square `box` on an RGBA canvas."""
    x0, y0, x1, y1 = box
    s = x1 - x0
    ox, oy = x0, y0

    # Paper first, so the body overlaps its lower part.
    paper = _paper_layer(canvas.size[0], pal, box)
    canvas.alpha_composite(paper)

    d = ImageDraw.Draw(canvas)

    # Main body.
    bx0, by0 = ox + 0.05 * s, oy + 0.32 * s
    bx1, by1 = ox + 0.95 * s, oy + 0.90 * s
    _rr(d, (bx0, by0, bx1, by1), 0.07 * s, pal["body"])

    # Output slot mouth near the top edge.
    _rr(d, (ox + 0.20 * s, oy + 0.325 * s, ox + 0.80 * s, oy + 0.36 * s),
        0.018 * s, pal["slot"])

    # Control panel with LED.
    _rr(d, (ox + 0.58 * s, oy + 0.41 * s, ox + 0.90 * s, oy + 0.53 * s),
        0.035 * s, pal["panel"])
    led_cx, led_cy, led_r = ox + 0.645 * s, oy + 0.47 * s, 0.028 * s
    d.ellipse((led_cx - led_r, led_cy - led_r, led_cx + led_r, led_cy + led_r),
              fill=pal["accent"])

    # Cassette at the bottom.
    _rr(d, (ox + 0.09 * s, oy + 0.68 * s, ox + 0.91 * s, oy + 0.87 * s),
        0.05 * s, pal["cassette"])
    # Divider seam.
    _rr(d, (bx0, oy + 0.655 * s, bx1, oy + 0.685 * s), 0.015 * s, pal["slot"])
    # Handle notch.
    _rr(d, (ox + 0.42 * s, oy + 0.755 * s, ox + 0.58 * s, oy + 0.79 * s),
        0.018 * s, pal["slot"])

    return canvas


def make_icon(size, pal):
    big = size * SS
    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    pad = 0.06 * big
    draw_printer(canvas, (pad, pad, big - pad, big - pad), pal)
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def _load_font(px):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, px)
    raise SystemExit("No usable bold TTF font found; checked: %s"
                     % ", ".join(FONT_CANDIDATES))


def make_logo(w, h, pal):
    big_w, big_h = w * SS, h * SS
    canvas = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))

    # Pictogram square on the left.
    margin = 0.06 * big_h
    pict = big_h - 2 * margin
    draw_printer(canvas, (margin, margin, margin + pict, margin + pict), pal)

    # Text to the right, vertically centred.
    d = ImageDraw.Draw(canvas)
    text = "Canon Printer"
    tx = margin + pict + 0.09 * big_h
    avail_w = big_w - tx - margin
    target = 0.42 * big_h
    font = _load_font(int(target))
    while d.textlength(text, font=font) > avail_w and target > 8:
        target *= 0.95
        font = _load_font(int(target))
    tb = d.textbbox((0, 0), text, font=font)
    th = tb[3] - tb[1]
    ty = (big_h - th) / 2 - tb[1]
    d.text((tx, ty), text, font=font, fill=pal["text"])

    return canvas.resize((w, h), Image.Resampling.LANCZOS)


def save(img, name):
    path = os.path.join(BRAND_DIR, name)
    img.save(path, "PNG")
    return path


def main():
    os.makedirs(BRAND_DIR, exist_ok=True)
    produced = []

    produced.append(save(make_icon(256, LIGHT), "icon.png"))
    produced.append(save(make_icon(512, LIGHT), "icon@2x.png"))
    produced.append(save(make_icon(256, DARK), "dark_icon.png"))
    produced.append(save(make_icon(512, DARK), "dark_icon@2x.png"))

    produced.append(save(make_logo(1024, 256, LIGHT), "logo.png"))
    produced.append(save(make_logo(2048, 512, LIGHT), "logo@2x.png"))
    produced.append(save(make_logo(1024, 256, DARK), "dark_logo.png"))
    produced.append(save(make_logo(2048, 512, DARK), "dark_logo@2x.png"))

    for p in produced:
        with Image.open(p) as im:
            print("%-14s %sx%s %s" % (os.path.basename(p), im.width, im.height,
                                      im.mode))


if __name__ == "__main__":
    sys.exit(main())
