"""
splash_screen.py
LifeCare Pharmacy ERP - animated Welcome screen shown between a successful
Login and the Dashboard opening.

Why this exists: the user asked for a stylish, animated "welcome" moment
after Login and before the Dashboard. After seeing a competitor app
("BharatERP")'s purple/violet splash screen alongside their own screens,
they asked for that look specifically on the WELCOME SCREEN ONLY - they
were explicit that the Dashboard and the rest of the app must keep the
existing Classic Blue theme untouched ("welcome scree only want change").
So this file, and only this file's own assets, use the new purple/violet
palette - theme.py (used everywhere else in the app) is not touched.

Design choices, and why:
- assets/splash_logo_purple.gif carries the LOGO artwork: a gentle pulse +
  soft purple/violet glow + twinkling sparkles, built from the app's real
  existing assets/brand_mark.png heart+hand logo (same generation approach
  as the original blue splash_logo.gif, just recolored) - not a new/
  invented logo, and no paid asset/library needed (all done with Pillow).
- assets/splash_bg.png is a vertical deep-violet -> vivid-purple gradient,
  generated once with Pillow, used as the whole card's background so the
  BharatERP-style bold/colourful look doesn't depend on Tk (which can't
  draw gradients natively).
- Everything is drawn on a single tk.Canvas (background image + logo
  frames + title/subtitle/dots text) instead of stacking separate Label
  widgets on top of the gradient image. Plain Tk widgets are opaque
  rectangles with one flat bg colour, so a Label placed over a gradient
  would show a visible seam where its flat colour doesn't match the
  gradient underneath it at that exact pixel row; a Canvas has no such
  problem since text/images are drawn directly onto the same image.
- The "Life Care Pharmacy ERP" title/subtitle are still native Tk (canvas)
  text in Segoe UI (Bold/Semibold), not baked into the GIF's pixels, for
  the same DPI-crispness reason as before - Segoe UI is guaranteed on
  every Windows PC this app targets (main.py's SetProcessDpiAwareness
  call handles the DPI scaling).
- overrideredirect(True) (no title bar/border) + centered + topmost, so it
  reads as a proper splash/welcome screen and not just another window.
- Fades in and back out via the Tk `-alpha` window attribute for a soft
  appearance/disappearance instead of an abrupt pop - wrapped in
  try/except since -alpha isn't guaranteed on every Tk build, but IS
  supported on Windows (this app's only real target).
"""

import tkinter as tk

from app_paths import app_path
import ui_style

try:
    from PIL import Image, ImageTk
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False


class SplashScreen:

    # Bigger than the original 400x480 for the bolder BharatERP-style
    # branding the user asked for ("bull page look like occupide good
    # feeling") - a larger, more confident welcome card.
    WIDTH = 460
    HEIGHT = 560
    FADE_STEP_MS = 15
    FADE_STEP = 0.09
    TOTAL_DURATION_MS = 2600   # how long the splash stays fully visible

    # BharatERP-inspired purple/violet palette - Welcome screen ONLY.
    BG_FALLBACK = "#4A148C"        # deep violet, used if splash_bg.png is missing
    TITLE_COLOR = "#FFFFFF"
    SUBTITLE_COLOR = "#E1BEE7"     # soft lavender
    DOTS_COLOR = "#FFFFFF"
    BORDER_COLOR = "#CE93D8"       # light purple accent border

    def __init__(self, parent, on_done, duration_ms=None):
        """parent: the (withdrawn) Login root - kept alive so this runs on
        its already-active mainloop. on_done: called exactly once, after
        the fade-out finishes and this window is destroyed (normally
        LoginWindow's launch_dashboard). If the GIF/background assets or
        Pillow are missing for any reason, this still shows a plain
        solid-purple text-only welcome screen for the same duration
        rather than skipping straight to the Dashboard - a missing
        animation asset should never be the reason the whole flow
        breaks."""
        self._on_done = on_done
        self._active = True
        self._frames = []
        self._frame_durations = []
        self._frame_idx = 0
        self._dot_idx = 0
        self._gif_canvas_id = None
        self._bg_photo = None
        # Tracks the pending .after() id for each recurring loop so it can
        # be explicitly cancelled the moment the splash starts finishing -
        # see _begin_finish()/_cancel_after() for why this matters.
        self._gif_after_id = None
        self._dots_after_id = None
        self._fade_after_id = None

        self.win = tk.Toplevel(parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self._set_alpha(0.0)

        # Screen-centering math consolidated into ui_style.center_window()
        # (Aug 2026, after Login was found opening top-left) - same
        # result as before, just no longer a copy kept only here.
        ui_style.center_window(self.win, self.WIDTH, self.HEIGHT)

        # Thin light-purple border frame around the whole card, since an
        # overrideredirect() window has no native border of its own.
        border = tk.Frame(self.win, bg=self.BORDER_COLOR)
        border.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            border, width=self.WIDTH - 4, height=self.HEIGHT - 4,
            highlightthickness=0, bg=self.BG_FALLBACK
        )
        self.canvas.pack(padx=2, pady=2)

        self._draw_background()

        gif_cx = (self.WIDTH - 4) // 2
        gif_cy = 190
        self._gif_cx, self._gif_cy = gif_cx, gif_cy

        self.canvas.create_text(
            gif_cx, 360, text="Life Care Pharmacy ERP",
            fill=self.TITLE_COLOR, font=("Segoe UI Semibold", 24, "bold")
        )
        self.canvas.create_text(
            gif_cx, 396, text="Billing and Inventory Management",
            fill=self.SUBTITLE_COLOR, font=("Segoe UI", 11)
        )
        self.dots_id = self.canvas.create_text(
            gif_cx, 440, text="", fill=self.DOTS_COLOR,
            font=("Segoe UI", 18, "bold")
        )

        self._load_frames()
        self._animate_gif()
        self._animate_dots()
        self._fade_in()

        total = duration_ms if duration_ms is not None else self.TOTAL_DURATION_MS
        self.win.after(total, self._begin_finish)

    # ------------------------------------------------------------------
    def _draw_background(self):
        if not _PIL_AVAILABLE:
            return
        try:
            img = Image.open(app_path("assets", "splash_bg.png")).convert("RGB")
            img = img.resize((self.WIDTH - 4, self.HEIGHT - 4), Image.LANCZOS)
            self._bg_photo = ImageTk.PhotoImage(img)
            self.canvas.create_image(0, 0, anchor="nw", image=self._bg_photo)
        except Exception:
            # Falls back to the solid BG_FALLBACK colour set on the
            # canvas itself above - still a valid (just flat) purple card.
            pass

    def _set_alpha(self, value):
        try:
            self.win.attributes("-alpha", value)
        except Exception:
            pass

    def _load_frames(self):
        if not _PIL_AVAILABLE:
            return
        path = app_path("assets", "splash_logo_purple.gif")
        try:
            img = Image.open(path)
            while True:
                frame = img.convert("RGBA")
                self._frames.append(ImageTk.PhotoImage(frame))
                self._frame_durations.append(max(20, img.info.get("duration", 40)))
                img.seek(img.tell() + 1)
        except EOFError:
            pass
        except Exception:
            self._frames = []
            self._frame_durations = []

    def _animate_gif(self):
        if not self._active:
            return
        if self._frames:
            frame = self._frames[self._frame_idx]
            if self._gif_canvas_id is None:
                self._gif_canvas_id = self.canvas.create_image(
                    self._gif_cx, self._gif_cy, image=frame
                )
            else:
                self.canvas.itemconfig(self._gif_canvas_id, image=frame)
            delay = self._frame_durations[self._frame_idx]
            self._frame_idx = (self._frame_idx + 1) % len(self._frames)
        else:
            delay = 200
        try:
            self._gif_after_id = self.win.after(delay, self._animate_gif)
        except Exception:
            pass

    def _animate_dots(self):
        if not self._active:
            return
        try:
            self.canvas.itemconfig(self.dots_id, text="." * ((self._dot_idx % 3) + 1))
        except Exception:
            pass
        self._dot_idx += 1
        try:
            self._dots_after_id = self.win.after(400, self._animate_dots)
        except Exception:
            pass

    def _fade_in(self, step=0):
        if not self._active:
            return
        alpha = min(1.0, step * self.FADE_STEP)
        self._set_alpha(alpha)
        if alpha < 1.0:
            self._fade_after_id = self.win.after(self.FADE_STEP_MS, lambda: self._fade_in(step + 1))

    def _cancel_after(self, after_id):
        # Real-machine bug fix (2026-08-22): the gif/dots loops each keep
        # exactly one .after() call pending at all times (that's how they
        # re-trigger themselves). If login.py's on_done callback (normally
        # launch_dashboard) destroys the Login root - which tears down its
        # whole Tcl interpreter - while one of those calls is still queued,
        # Tcl later tries to fire a callback name that no longer exists
        # anywhere, raising "invalid command name ... _animate_dots" from
        # deep inside Tk's own event loop (the user hit this on their real
        # Windows machine). Checking self._active inside the callback is
        # NOT enough to prevent it, because that check only runs if Tcl
        # can still find and invoke the command in the first place - once
        # the interpreter is gone, Tcl errors out before any Python code
        # runs. So instead we explicitly cancel every outstanding .after()
        # call the moment the splash starts finishing, before the parent
        # window (and its interpreter) can possibly be destroyed.
        if after_id is None:
            return
        try:
            self.win.after_cancel(after_id)
        except Exception:
            pass

    def _begin_finish(self):
        self._active = False
        # Cancel the still-pending gif/dots timers immediately - see
        # _cancel_after()'s docstring for why this can't wait until after
        # the fade-out finishes.
        self._cancel_after(self._gif_after_id)
        self._cancel_after(self._dots_after_id)
        self._gif_after_id = None
        self._dots_after_id = None
        self._fade_out()

    def _fade_out(self, step=0):
        alpha = max(0.0, 1.0 - step * self.FADE_STEP)
        self._set_alpha(alpha)
        if alpha > 0.0:
            self._fade_after_id = self.win.after(self.FADE_STEP_MS, lambda: self._fade_out(step + 1))
        else:
            self._finish()

    def _finish(self):
        # Belt-and-braces: cancel any leftover timer before destroy(), in
        # case a future edit ever adds one that isn't already cancelled
        # above.
        self._cancel_after(self._fade_after_id)
        self._fade_after_id = None
        try:
            self.win.destroy()
        except Exception:
            pass
        self._on_done()
