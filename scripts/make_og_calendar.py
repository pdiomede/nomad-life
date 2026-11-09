"""Draw static/img/og-calendar.png (1200x630): the link preview (Open Graph image) of a public
calendar link (/c/<token>). The Nomad Life logo and name, "View my shared calendar", and a
January the way the calendar shows it, in the app's own colors and fonts. Nothing of any
account is in it: every public link shares this one image.

    .venv/bin/python scripts/make_og_calendar.py
"""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1200, 630
img = Image.new("RGB", (W, H), "#2B2D3C")
d = ImageDraw.Draw(img)


def font(name, size, weight=None):
    f = ImageFont.truetype(f"{ROOT}/static/fonts/web/{name}.woff2", size)
    if weight:
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    return f


outfit = font("outfit-latin", 66)
inter = lambda size, w=400: font("inter-latin", size, w)

# Left: the logo, the name, what the link is.
logo = Image.open(f"{ROOT}/static/img/logo-mark.png").convert("RGBA")
lh = 110
logo = logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS)
img.paste(logo, (72, 150), logo)
d.text((72, 292), "Nomad Life", font=outfit, fill="#FFFFFF")
d.text((74, 386), "View my shared calendar", font=inter(38, 600), fill="#E4E5EC")
d.text((74, 440), "Countries and days, read only", font=inter(26), fill="#A5A8B6")

# Right: a white card with January as the calendar shows it.
cx, cy, cw, ch = 640, 87, 500, 456
d.rounded_rectangle((cx, cy, cx + cw, cy + ch), radius=22, fill="#FFFFFF")
pad, gap = 34, 8
cell = (cw - 2 * pad - 6 * gap) // 7
x0, y = cx + pad, cy + 30
d.text((x0, y), "January", font=inter(32, 600), fill="#A13A8C")
y += 58
week = inter(22, 500)
for i, letter in enumerate("MTWTFSS"):
    w = d.textlength(letter, font=week)
    d.text((x0 + i * (cell + gap) + (cell - w) / 2, y), letter, font=week, fill="#62677B")
y += 40
blue, red, pink = "#4756C4", "#CF4545", "#EC7AA2"
color = lambda n: pink if n >= 29 else blue if n <= 8 or 24 <= n <= 28 else red
num = inter(22, 500)
col = 4  # 1 January 2027 is a Friday
row_y = y
for n in range(1, 32):
    x = x0 + col * (cell + gap)
    c = color(n)
    d.rounded_rectangle((x, row_y, x + cell, row_y + cell), radius=10, fill=c)
    t = str(n)
    w = d.textlength(t, font=num)
    d.text((x + (cell - w) / 2, row_y + (cell - 26) / 2), t, font=num,
           fill="#1B2030" if c == pink else "#FFFFFF")
    col += 1
    if col == 7:
        col, row_y = 0, row_y + cell + gap

img.save(f"{ROOT}/static/img/og-calendar.png", optimize=True)
print("saved", img.size)
