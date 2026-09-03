"""
generate_icons.py
LifeCare Pharmacy ERP - one-time/maintenance script that draws this
app's small icon set as real PNG files (icons/*.png), instead of relying
on emoji glyphs or a proprietary icon font (Segoe MDL2/Fluent icons).

WHY PNGs instead of an icon font: Segoe MDL2 Assets/Fluent Icons map
each icon to a private-use Unicode codepoint (e.g. Home = U+E80F) that
only resolves correctly if you have the EXACT right codepoint memorized -
get one wrong and it silently renders as a blank tofu box, which is
worse than the colourful-but-working emoji this is meant to replace.
Drawing the icons ourselves with Pillow removes that whole class of risk
- what you see in icons/*.png after running this script IS what the app
will show, no font-resolution guesswork involved.

Every icon is drawn WHITE on a transparent background at 4x supersample
(96x96) then downsampled to 22x22 with LANCZOS for clean anti-aliased
edges, matching the app's existing convention of white icon/text on a
coloured button or the dark navy sidebar (bg="#1e2a38") - every place
these icons get used already has a coloured/dark background behind them.

Run manually whenever the icon set needs to change:
    python generate_icons.py
Output goes to ./icons/ (created if missing) - that folder must ship
alongside the .exe in a PyInstaller build, same as the Invoices/ folder
app_paths.py already creates at runtime (see app_paths.app_path()).
"""

import math
import os
from PIL import Image, ImageDraw

SUPER = 4
SIZE = 22
CANVAS = SIZE * SUPER
STROKE = 5 * SUPER // 4  # ~5px effective stroke at final size
WHITE = (255, 255, 255, 255)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
os.makedirs(OUT_DIR, exist_ok=True)


def _new_canvas():
    return Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))


def _save(img, name):
    small = img.resize((SIZE, SIZE), Image.LANCZOS)
    small.save(os.path.join(OUT_DIR, f"{name}.png"))
    print(f"wrote icons/{name}.png")


def draw_home():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    m = CANVAS * 0.1
    roof_tip = (CANVAS / 2, m)
    d.line([roof_tip, (m, CANVAS * 0.5), (CANVAS - m, CANVAS * 0.5), roof_tip],
           fill=WHITE, width=STROKE, joint="curve")
    body = [m + CANVAS * 0.05, CANVAS * 0.5, CANVAS - m - CANVAS * 0.05, CANVAS - m]
    d.rectangle(body, outline=WHITE, width=STROKE)
    door_w = CANVAS * 0.16
    d.rectangle(
        [CANVAS / 2 - door_w / 2, CANVAS - m - CANVAS * 0.32, CANVAS / 2 + door_w / 2, CANVAS - m],
        outline=WHITE, width=STROKE
    )
    _save(img, "home")


def draw_package():
    """Cardboard box with a taped "+" seam - reads as "inventory/stock"
    without needing a 3D-cube illusion, which is fiddly at 22px."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    m = CANVAS * 0.14
    d.rectangle([m, m, CANVAS - m, CANVAS - m], outline=WHITE, width=STROKE)
    d.line([(CANVAS / 2, m), (CANVAS / 2, CANVAS - m)], fill=WHITE, width=STROKE)
    d.line([(m, CANVAS / 2), (CANVAS - m, CANVAS / 2)], fill=WHITE, width=int(STROKE * 0.6))
    _save(img, "package")


def draw_money():
    """Receipt/bill shape (rect + 3 lines) - avoids needing a font with
    a rupee glyph baked into a small raster icon."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    m = CANVAS * 0.16
    d.rounded_rectangle([m, m * 0.6, CANVAS - m, CANVAS - m * 0.6], radius=CANVAS * 0.06,
                         outline=WHITE, width=STROKE)
    for i, frac in enumerate((0.38, 0.55, 0.72)):
        w = CANVAS * (0.44 if i < 2 else 0.28)
        y = CANVAS * frac
        d.line([(CANVAS / 2 - w / 2, y), (CANVAS / 2 + w / 2, y)], fill=WHITE, width=int(STROKE * 0.7))
    _save(img, "money")


def draw_people():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    for cx in (CANVAS * 0.36, CANVAS * 0.64):
        r = CANVAS * 0.11
        d.ellipse([cx - r, CANVAS * 0.18, cx + r, CANVAS * 0.18 + 2 * r], outline=WHITE, width=STROKE)
    d.arc([CANVAS * 0.08, CANVAS * 0.5, CANVAS * 0.62, CANVAS * 1.05], start=180, end=360,
          fill=WHITE, width=STROKE)
    d.arc([CANVAS * 0.38, CANVAS * 0.5, CANVAS * 0.92, CANVAS * 1.05], start=180, end=360,
          fill=WHITE, width=STROKE)
    _save(img, "people")


def draw_chart():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    base = CANVAS - CANVAS * 0.14
    bar_w = CANVAS * 0.16
    bars = [(CANVAS * 0.2, 0.35), (CANVAS * 0.45, 0.6), (CANVAS * 0.7, 0.85)]
    for x, h in bars:
        d.rectangle([x, base - CANVAS * h, x + bar_w, base], outline=WHITE, width=STROKE)
    d.line([(CANVAS * 0.08, base), (CANVAS * 0.92, base)], fill=WHITE, width=STROKE)
    _save(img, "chart")


def draw_settings():
    """Three sliders (Fluent-style "settings" glyph) instead of a gear -
    a gear needs 8 precisely-angled teeth to read clearly at 22px; three
    lines with offset toggle dots is unambiguous at any size and just as
    recognisable a "settings" symbol."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    xs = [0.3, 0.6, 0.4]
    for i, y_frac in enumerate((0.28, 0.5, 0.72)):
        y = CANVAS * y_frac
        d.line([(CANVAS * 0.08, y), (CANVAS * 0.92, y)], fill=WHITE, width=int(STROKE * 0.7))
        cx = CANVAS * xs[i]
        r = CANVAS * 0.08
        d.ellipse([cx - r, y - r, cx + r, y + r], fill=WHITE)
    _save(img, "settings")


def draw_chat():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    m = CANVAS * 0.12
    bubble = [m, m, CANVAS - m, CANVAS * 0.72]
    d.rounded_rectangle(bubble, radius=CANVAS * 0.12, outline=WHITE, width=STROKE)
    d.polygon(
        [(CANVAS * 0.28, CANVAS * 0.68), (CANVAS * 0.22, CANVAS * 0.92), (CANVAS * 0.44, CANVAS * 0.7)],
        fill=WHITE
    )
    _save(img, "chat")


def draw_download():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    cx = CANVAS / 2
    d.line([(cx, CANVAS * 0.12), (cx, CANVAS * 0.62)], fill=WHITE, width=STROKE)
    d.polygon(
        [(cx - CANVAS * 0.2, CANVAS * 0.46), (cx + CANVAS * 0.2, CANVAS * 0.46), (cx, CANVAS * 0.72)],
        fill=WHITE
    )
    d.line([(CANVAS * 0.14, CANVAS * 0.88), (CANVAS * 0.86, CANVAS * 0.88)], fill=WHITE, width=STROKE)
    _save(img, "download")


def draw_refresh():
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    box = [CANVAS * 0.12, CANVAS * 0.12, CANVAS * 0.88, CANVAS * 0.88]
    d.arc(box, start=-40, end=200, fill=WHITE, width=STROKE)
    d.arc(box, start=140, end=380, fill=WHITE, width=STROKE)
    d.polygon(
        [(CANVAS * 0.86, CANVAS * 0.22), (CANVAS * 0.98, CANVAS * 0.32), (CANVAS * 0.80, CANVAS * 0.40)],
        fill=WHITE
    )
    d.polygon(
        [(CANVAS * 0.14, CANVAS * 0.78), (CANVAS * 0.02, CANVAS * 0.68), (CANVAS * 0.20, CANVAS * 0.60)],
        fill=WHITE
    )
    _save(img, "refresh")


def draw_moon():
    """Crescent moon - Phase 4's dark-mode toggle icon (shown when the app
    is currently in light mode, click to switch to dark). Drawn as one
    solid circle with a second, offset circle 'punched' out using a fully
    transparent fill - the standard PIL way to get a crescent shape
    without an icon-font glyph."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    r = CANVAS * 0.34
    cx, cy = CANVAS * 0.54, CANVAS * 0.5
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    cut_r = CANVAS * 0.30
    cut_cx, cut_cy = CANVAS * 0.68, CANVAS * 0.38
    d.ellipse([cut_cx - cut_r, cut_cy - cut_r, cut_cx + cut_r, cut_cy + cut_r], fill=(0, 0, 0, 0))
    _save(img, "moon")


def draw_sun():
    """Sun - Phase 4's toggle icon shown when the app is currently in dark
    mode (click to switch back to light). Center circle outline plus 8
    short rays at 45-degree intervals."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    cx, cy = CANVAS / 2, CANVAS / 2
    r = CANVAS * 0.22
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=WHITE, width=STROKE)
    ray_inner = r + CANVAS * 0.08
    ray_outer = r + CANVAS * 0.22
    for angle_deg in range(0, 360, 45):
        angle = math.radians(angle_deg)
        x1 = cx + ray_inner * math.cos(angle)
        y1 = cy + ray_inner * math.sin(angle)
        x2 = cx + ray_outer * math.cos(angle)
        y2 = cy + ray_outer * math.sin(angle)
        d.line([(x1, y1), (x2, y2)], fill=WHITE, width=int(STROKE * 0.7))
    _save(img, "sun")


def draw_user():
    """Single person (login screen's Username field icon) - a bigger,
    centered version of one of the two figures in draw_people(), since a
    two-head icon reads oddly next to a single-user text field."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    cx = CANVAS / 2
    r = CANVAS * 0.16
    d.ellipse([cx - r, CANVAS * 0.14, cx + r, CANVAS * 0.14 + 2 * r], outline=WHITE, width=STROKE)
    d.arc([CANVAS * 0.14, CANVAS * 0.42, CANVAS * 0.86, CANVAS * 1.05], start=180, end=360, fill=WHITE, width=STROKE)
    _save(img, "user")


def draw_lock():
    """Padlock (login screen's Password field icon) - rounded rectangle
    body with an arc shackle and a small keyhole dot."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    body = [CANVAS * 0.2, CANVAS * 0.46, CANVAS * 0.8, CANVAS * 0.88]
    d.rounded_rectangle(body, radius=CANVAS * 0.05, outline=WHITE, width=STROKE)
    d.arc([CANVAS * 0.3, CANVAS * 0.12, CANVAS * 0.7, CANVAS * 0.58], start=180, end=360, fill=WHITE, width=STROKE)
    cx = CANVAS / 2
    d.ellipse([cx - CANVAS * 0.045, CANVAS * 0.6, cx + CANVAS * 0.045, CANVAS * 0.69], fill=WHITE)
    _save(img, "lock")


def draw_eye():
    """Password-hidden state (click to reveal) - flattened-ellipse eye
    outline with a solid pupil dot. A true almond/lens shape needs two
    intersecting circles, which plain ImageDraw can't boolean-combine -
    a squashed ellipse is the standard simplified substitute at this
    icon size and reads clearly as an eye."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    box = [CANVAS * 0.08, CANVAS * 0.28, CANVAS * 0.92, CANVAS * 0.72]
    d.ellipse(box, outline=WHITE, width=STROKE)
    cx, cy = CANVAS / 2, CANVAS / 2
    r = CANVAS * 0.09
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    _save(img, "eye")


def draw_eye_off():
    """Password-visible state (click to hide) - same eye shape with a
    diagonal strike-through, the standard 'hide' convention."""
    img = _new_canvas()
    d = ImageDraw.Draw(img)
    box = [CANVAS * 0.08, CANVAS * 0.28, CANVAS * 0.92, CANVAS * 0.72]
    d.ellipse(box, outline=WHITE, width=STROKE)
    cx, cy = CANVAS / 2, CANVAS / 2
    r = CANVAS * 0.09
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WHITE)
    d.line([(CANVAS * 0.1, CANVAS * 0.85), (CANVAS * 0.9, CANVAS * 0.15)], fill=WHITE, width=STROKE)
    _save(img, "eye_off")


if __name__ == "__main__":
    draw_home()
    draw_package()
    draw_money()
    draw_people()
    draw_chart()
    draw_settings()
    draw_chat()
    draw_download()
    draw_refresh()
    draw_moon()
    draw_sun()
    draw_user()
    draw_lock()
    draw_eye()
    draw_eye_off()
    print(f"\n{15} icons written to {OUT_DIR}")
