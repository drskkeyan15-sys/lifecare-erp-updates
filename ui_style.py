"""
ui_style.py
LifeCare Pharmacy ERP - shared Treeview styling ("spreadsheet" look)

Centralizes the ttk.Style() setup that used to be copy-pasted separately
into stock.py and medicine_master.py (and was missing entirely from
purchase.py and Bulk Import's review grid, which is why Purchase Entry's
item table looked like a plain unstyled list next to the other two
screens). One definition here means a future color/font tweak only
needs to happen in one place, and every table that calls it gets a
consistent, on-brand look instead of each screen inventing its own.

Kept deliberately independent of any one screen: pure ttk.Style/Treeview
calls, no database or business logic, so it's safe to import from any
module without pulling in unrelated dependencies.
"""

from tkinter import ttk
import tkinter as tk
from tkinter import filedialog, messagebox
import os
import tempfile
import webbrowser
import theme

# Matches the blue already used for every title bar and primary button
# across the app (Dashboard, Purchase, Settings, etc.) - reusing it here
# instead of the previous plain grey (#E0E0E0) header makes the tables
# read as part of the same app instead of a separate, undecorated list.
# Sourced from theme.py (single source of truth for every color in the
# app) rather than a hardcoded hex literal - see that file's docstring.
HEADER_BG = theme.TABLE_HEADER_BG
HEADER_FG = theme.TABLE_HEADER_FG

# Unchanged from the zebra-striping already in stock.py/medicine_master.py -
# reused rather than inventing a new shade, so tables that already had
# this look keep looking exactly the same, and purchase.py/Bulk Import's
# review grid (which had no zebra striping at all before) now match them.
ROW_EVEN_BG = theme.TABLE_ROW_EVEN
ROW_ODD_BG = theme.TABLE_ROW_ODD

# No table anywhere in the app customized selection color before this
# (every one fell back to the OS/theme default) - an on-brand light
# blue with dark blue text is easier to spot in a busy row and reads as
# intentional rather than a generic system highlight.
SELECT_BG = theme.TABLE_SELECT_BG
SELECT_FG = theme.TABLE_SELECT_FG

ROW_HEIGHT = 28
CELL_FONT = ("Segoe UI", 10)
HEADER_FONT = ("Segoe UI", 10, "bold")

_styled = False


def setup_excel_style():
    """
    Configures the ttk "Treeview" / "Treeview.Heading" styles to look
    like a real spreadsheet - bold coloured header, visible cell
    borders, comfortable row height, on-brand selection colour -
    instead of each OS's plain default list look.

    Idempotent and safe to call from every screen's create_ui()/
    create_table(): ttk styles are global (there's one "Treeview" style
    shared by every Treeview widget in the process unless a widget asks
    for a different named style), so only the FIRST call does real
    work - repeat calls from other screens that also import this module
    are cheap no-ops, not redundant re-styling.

    Uses the "clam" theme specifically because Windows' default themes
    ("vista"/"winnative") silently ignore Treeview borderwidth/relief
    settings - clam is the one built-in Tk theme that actually renders
    them, which is what makes the cell grid lines visible at all.
    """
    global _styled
    if _styled:
        return
    _styled = True

    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(
        "Treeview",
        rowheight=ROW_HEIGHT,
        fieldbackground="white",
        background="white",
        # Matches theme.TABLE_GRID, the same darker mid-grey
        # make_excel_sheet() below already uses for tksheet's grid lines
        # (that comment explains #D0D0D0 was "barely visible against
        # white") - this ttk.Treeview style had been left on the old,
        # already-flagged-as-too-light shade, so plain Treeview tables
        # (Purchase, Customers, ...) had fainter grid lines than the
        # tksheet-based ones (Medicine Master, Stock) right next to them.
        bordercolor=theme.TABLE_GRID,
        borderwidth=1,
        relief="solid",
        font=CELL_FONT,
    )
    style.configure(
        "Treeview.Heading",
        font=HEADER_FONT,
        background=HEADER_BG,
        foreground=HEADER_FG,
        relief="flat",
        padding=(6, 6),
    )
    # clam ignores a plain background= on Heading hover/press states
    # without an explicit map() - without this the header flashes back
    # to the theme's default grey the moment the mouse moves over it.
    style.map(
        "Treeview.Heading",
        background=[("active", HEADER_BG), ("pressed", HEADER_BG)],
        foreground=[("active", HEADER_FG), ("pressed", HEADER_FG)],
    )
    style.map(
        "Treeview",
        background=[("selected", SELECT_BG)],
        foreground=[("selected", SELECT_FG)],
    )


# --------------------------------------------------------------
# Rounded-rectangle Canvas primitive (Phase 3/4 visual polish + the
# login screen redesign)
# --------------------------------------------------------------
# Tkinter has no border-radius option on any widget - this traces a
# 12-point polygon around the corners and draws it with smooth=True,
# asking Tk to spline through the points instead of straight segments.
# The standard/well-established Tk recipe for a "rounded rectangle"
# look. Shared here (rather than copy-pasted per screen) so
# dashboard.py's KPI cards and login.py's field pills draw identical
# corners from one definition instead of two copies drifting apart.
def center_window(win, width=None, height=None, parent=None):
    """
    Centers `win` (a Tk root or a Toplevel dialog/popup) and sets its
    size in one geometry() call - the reusable version of the screen-
    centering math splash_screen.py already had, and the parent-centering
    math dashboard.py's Factory Reset dialog fix (Aug 2026) hand-rolled
    inline. Pulled out here per an explicit user request after the LOGIN
    window was found opening at the top-left corner of the screen: Login
    only ever called win.geometry("460x460") - a size with no x/y offset
    at all, so it just resized the window wherever the OS/window manager
    happened to have placed it (typically the top-left) instead of
    actually centering it.

    width/height: the window's fixed size. If omitted, uses
    win.winfo_reqwidth()/reqheight() AFTER win.update_idletasks() - i.e.
    "size it to whatever its own packed widgets need" - so this also
    works for a dialog that doesn't have (or shouldn't have) a hardcoded
    guessed size (see dashboard.py's Factory Reset dialog docstring for
    why hardcoding a size BEFORE packing widgets is its own separate bug
    class: real content can end up taller than the guess and get pushed
    off-screen entirely).

    parent: if given, centers over the PARENT window's current bounds
    (winfo_x/y/width/height) instead of the monitor - this is what makes
    a popup read as "centered over the screen it was opened from" rather
    than "centered on the monitor regardless of where the app actually
    is", which matters most on a multi-monitor setup. Omit `parent` for
    a window with no meaningful owner yet (the Login window itself, or
    the post-login loading splash) to center on the screen instead.

    FIX (2026-08-27, user report: "small popup window open and close
    then [the real] window open" - first noticed on Bulk Purchase
    Import, but this was a bug in THIS shared function, not that one
    screen): every real call site in the app (~30 of them, grepped
    2026-08-27) passes an explicit width AND height - none actually
    rely on the winfo_reqwidth()/reqheight() fallback below. The
    unconditional win.update_idletasks() that used to run FIRST, before
    geometry() was ever set, forced Tk to actually realize/paint the
    brand new Toplevel at its OS-default position and tiny default size
    for one visible frame - THEN this function would compute the real
    centered geometry and jump it there a moment later, which is
    exactly the "small window flashes, then the real one opens" report.
    Without this early flush, geometry() gets applied before Python
    ever hands control back to Tk's event loop, so the window is never
    actually drawn on screen in its wrong, pre-centered state at all.
    update_idletasks() is still needed - but ONLY - when width/height
    are omitted and reqwidth()/reqheight() must reflect real packed
    content.
    """
    if width is None or height is None:
        win.update_idletasks()
    w = width if width is not None else win.winfo_reqwidth()
    h = height if height is not None else win.winfo_reqheight()
    if parent is not None:
        base_x, base_y = parent.winfo_x(), parent.winfo_y()
        base_w, base_h = parent.winfo_width(), parent.winfo_height()
    else:
        base_x, base_y = 0, 0
        base_w, base_h = win.winfo_screenwidth(), win.winfo_screenheight()
    x = base_x + (base_w - w) // 2
    y = base_y + (base_h - h) // 2
    win.geometry(f"{w}x{h}+{max(x, 0)}+{max(y, 0)}")


# --------------------------------------------------------------
# Shared "modern popup" chrome (Aug 2026 app-wide popup visual refresh)
# --------------------------------------------------------------
# ui_popups.py introduced a colored-header / white-body / flat-button
# look for the app's messagebox-replacement dialogs (show_info,
# show_warning, ...). This app also has ~25 hand-built Toplevel popups
# of its own scattered across nearly every screen - "Selected X Info"
# panels, "Quick Edit" forms, invoice/purchase drill-downs, Export
# Settings, and so on - each previously built with plain grey Tk
# defaults. popup_header() and flat_button() below are the SAME look,
# pulled out here (not duplicated per-file, and not reaching into
# ui_popups.py's own private helpers across a module boundary) so every
# one of those popups can adopt it with a small, mechanical change
# instead of each screen hand-rolling its own header/button styling.
def popup_header(win, title, bg=None, icon=""):
    """
    Configures `win` (a Toplevel) with a white body background and
    packs a colored header strip (icon + title, white bold text) at the
    top. Returns a white-background body Frame, already packed with
    fill="both", expand=True, for the caller to pack its own content
    into - callers should build ALL their content into that body frame,
    not `win` directly, so backgrounds stay consistent.

    bg: header strip color - defaults to theme.PRIMARY (the brand blue
    used for every neutral/info popup); pass theme.STATUS_WARNING or
    theme.STATUS_DANGER for a warning/danger-flavored popup, matching
    ui_popups.py's own kind-to-color mapping.
    icon: optional single-character/emoji prefix before the title
    (ui_popups.py's own convention - e.g. "ℹ" for info, "⚠" for warning).
    """
    bg = bg or theme.PRIMARY
    win.configure(bg=theme.SURFACE_WHITE)
    header = tk.Frame(win, bg=bg)
    header.pack(fill="x")
    label_text = f"{icon}  {title}" if icon else title
    tk.Label(
        header, text=label_text, bg=bg, fg="white",
        font=("Segoe UI", 13, "bold"), anchor="w", padx=16, pady=12,
    ).pack(fill="x")
    body = tk.Frame(win, bg=theme.SURFACE_WHITE)
    body.pack(fill="both", expand=True)
    return body


def flat_button(parent, text, bg, command, fg="white", width=12):
    """
    Shared flat-button factory (relief='flat', hover highlight, hand2
    cursor) for the popup visual refresh above - one look for every
    button in every hand-built popup app-wide, mirroring
    ui_popups.py's own internal button style.
    """
    btn = tk.Button(
        parent, text=text, bg=bg, fg=fg, font=("Segoe UI", 10, "bold"),
        relief="flat", bd=0, padx=18, pady=8, width=width,
        activebackground=bg, activeforeground=fg,
        cursor="hand2", command=command,
    )
    hover_bg = theme.PRIMARY_HOVER if bg == theme.PRIMARY else bg
    resting_bg = bg
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover_bg))
    btn.bind("<Leave>", lambda e: btn.configure(bg=resting_bg))
    return btn


def round_rect(canvas, x1, y1, x2, y2, radius=20, **kwargs):
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


def apply_zebra_tags(tree):
    """
    Registers the standard alternating-row tags ("even"/"odd") used
    across the app. Call once per Treeview right after creating it,
    then tag each inserted row "even" or "odd" based on its row index
    (e.g. "even" if len(tree.get_children()) % 2 == 0 else "odd",
    checked BEFORE inserting the new row).

    Screens with their own semantic tags (Stock's "low"/"expired", Bulk
    Import's "new") should tag_configure those separately - later
    tag_configure calls for a different tag name don't conflict with
    this, and a row can carry both an even/odd tag and a semantic tag
    at once (Tk applies whichever tag's styling was configured most
    recently for each overlapping property).
    """
    tree.tag_configure("even", background=ROW_EVEN_BG)
    tree.tag_configure("odd", background=ROW_ODD_BG)


def next_row_tag(tree):
    """Returns "even" or "odd" for the NEXT row about to be inserted,
    based on how many rows the tree currently holds. Call this BEFORE
    tree.insert(), not after - it looks at the current child count."""
    return "even" if len(tree.get_children()) % 2 == 0 else "odd"


def style_columns(tree, columns, text_columns=()):
    """
    Right-aligns numeric/currency columns and left-aligns text columns -
    matching how a real spreadsheet reads (numbers ragged-left/aligned-
    right for easy magnitude comparison, text ragged-left) - instead of
    every column centered, which is how these tables looked before.

    `text_columns` names the columns that should stay left-aligned
    (medicine name, batch, expiry, status, etc.); every other column in
    `columns` is treated as numeric and right-aligned.
    """
    for col in columns:
        tree.column(col, anchor="w" if col in text_columns else "e")


# ttk.Treeview cannot draw real vertical grid lines between columns - a
# hard Tk limitation, not a styling gap - which is what a genuine
# "looks like Excel" table needs (see the Aug 2026 UI redesign
# conversation). make_excel_sheet() below builds a tksheet.Sheet
# instead, styled to match the Treeview tables above, for screens that
# need that real boxed-cell look. Both styling systems are kept side by
# side rather than migrating every table at once, so screens can move
# over one at a time and get verified against the real app before the
# next one changes.
CENTER_PAD_WIDTH = 20  # extra px added to every column so right/center
                        # -aligned numbers get breathing room instead of
                        # sitting flush against the cell border - tksheet
                        # has no separate cell-padding setting to reach
                        # for instead (checked against the installed
                        # library source), so a wider column is the
                        # available lever.


# Extra px reserved for the sheet's own vertical scrollbar
# (show_y_scrollbar defaults to True) so the last real column doesn't
# sit half-hidden behind it once the widget is sized to its exact
# content width instead of stretching to fill its container.
_SCROLLBAR_ALLOWANCE = 22


# DEPRECATED as of 2026-08-22 - no longer referenced by medicine_master.py
# or stock.py (kept defined, unused, in case anything else still imports
# it). This used to cap the "stretch the last column to fill leftover
# width" fix, on the theory that stretching a single column to fill ALL
# the remaining width would leave an oddly wide, sparse-looking column.
# In practice, on real maximized-window widths, that cap itself became
# the "table doesn't fill the screen / big blank white strip on the
# right" complaint the user explicitly asked to be fixed - the leftover
# space beyond the cap is genuinely blank container background, which
# reads far worse than a wide-but-still-gridded last column. Both
# screens now stretch the last column with no ceiling at all, matching
# brand_master_gui.py's own last-column stretch (which never had this
# cap in the first place).
MAX_STRETCH_COLUMN_WIDTH = 220


def sheet_total_width(columns, col_widths=None):
    """
    Sum of every column's on-screen width (+ CENTER_PAD_WIDTH per
    column, same padding make_excel_sheet applies) plus scrollbar
    allowance - the exact pixel width a Sheet needs to show all of
    `columns` with nothing cut off and nothing left over. Used both at
    construction (make_excel_sheet below) and whenever a table's column
    set changes at runtime (ui_style.SheetTreeAdapter, for Reports'
    per-report column switching) so the widget can be reconfigured to
    the new width instead of being left stretched to its container's
    full width - that stretch is what used to leave a large blank
    header-colored block trailing past the last real column.
    """
    col_widths = col_widths or {}
    return sum(col_widths.get(c, 120) + CENTER_PAD_WIDTH for c in columns) + _SCROLLBAR_ALLOWANCE


def make_excel_sheet(parent, columns, col_widths=None, text_columns=(), center_columns=()):
    """
    Creates a tksheet.Sheet styled to match setup_excel_style() above -
    same header colour, same zebra shade, same selection colour - so a
    screen using tksheet and a screen still on ttk.Treeview look like
    the same app.

    `col_widths` is an optional {column_name: px} dict (falls back to
    120px, tksheet's own default, for anything not listed).
    `text_columns` are left-aligned (names, batch numbers, dates);
    `center_columns` are centered (serial numbers - a plain count reads
    oddly jammed against a border when right-aligned in a narrow
    column, and isn't a magnitude anyone needs to compare, unlike a
    price or quantity); everything else right-aligns as a
    currency/quantity column would in a real spreadsheet.

    The widget is constructed with an explicit pixel width (the exact
    sum of its columns, via sheet_total_width()) rather than left to
    default/stretch - callers must pack it with fill="y" (NOT "both"/
    "x") so that requested width is actually honoured instead of being
    overridden by pack's own stretch-to-fill-parent behaviour. Tables
    with fewer columns than the window is wide (Stock, Smart Alerts)
    used to show a large blank header-colored block trailing past the
    last real column because the sheet was being force-stretched to the
    full container width - sizing it explicitly and packing with fill="y"
    only fixes that; the leftover space is now the plain container
    background instead.

    Deliberately does NOT call enable_bindings() - editable vs
    read-only, and which interactions to allow, differs per screen, so
    the caller wires that up itself right after this returns.
    """
    from tksheet import Sheet

    col_widths = col_widths or {}
    sheet = Sheet(
        parent,
        headers=list(columns),
        show_row_index=False,
        width=sheet_total_width(columns, col_widths),
        font=CELL_FONT + ("normal",),
        header_font=HEADER_FONT,
        header_bg=HEADER_BG,
        header_fg=HEADER_FG,
        table_bg="white",
        # Body cell borders - darkened from the previous #D0D0D0 (barely
        # visible against white) to a proper mid-grey, per explicit user
        # feedback wanting clearly visible Excel-style cell borders.
        table_grid_fg="#9E9E9E",
        # Header cell borders - tksheet's own default (header_grid_fg,
        # unset before this) is "#C4C7C5", a near-white grey meant for
        # tksheet's default pale header background. Against OUR custom
        # dark blue header_bg (#1565C0) that colour has almost no
        # contrast, so the header rendered as one solid undivided blue
        # block with no visible column separators - exactly the "Smart
        # Alerts looks broken/blank" report. White has real contrast
        # against dark blue and reads as an intentional grid, not a
        # rendering bug.
        header_grid_fg="white",
        header_border_fg="white",
        table_selected_cells_bg=SELECT_BG,
        table_selected_cells_fg=SELECT_FG,
        alternate_color=ROW_EVEN_BG,
    )

    for i, col in enumerate(columns):
        width = col_widths.get(col, 120) + CENTER_PAD_WIDTH
        sheet.column_width(column=i, width=width)
        if col in text_columns:
            align = "w"
        elif col in center_columns:
            align = "center"
        else:
            align = "e"
        sheet.align_columns(columns=[i], align=align, align_header=True)

    return sheet


class _SelectedCell:
    """Tiny stand-in for tksheet's get_currently_selected() return value -
    only the `.row` attribute is ever read by any caller in this app
    (verified by grep), so that's all this carries."""
    __slots__ = ("row",)

    def __init__(self, row):
        self.row = row


class PlainSheet(ttk.Treeview):
    """
    A real ttk.Treeview that answers to the same method names the
    tksheet-based screens (medicine_master.py, brand_master_gui.py,
    purchase.py) already call - pack()/enable_bindings()/
    get_currently_selected()/set_sheet_data()/get_sheet_data()/
    highlight_rows()/dehighlight_rows()/dehighlight_all()/del_rows()/
    column_width() - so those screens can get a plain, native Tk list
    widget (near-instant to build and populate) WITHOUT rewriting their
    own load_data()/select_record()/highlight logic. Every method here
    mirrors the exact tksheet API subset those three files use (checked
    against their real source, not guessed).

    2026-08-30: added at the user's explicit request after live-testing
    confirmed tksheet (a Canvas-drawn widget) is the actual cause of the
    ~0.4s "flash" opening Medicine Master/Brand Master/Purchase, while
    Billing (already plain ttk.Treeview) opens instantly. The user chose
    speed + simplicity over tksheet's real vertical grid lines and the
    "pad the grid so it looks full" cosmetic (MIN_VISIBLE_ROWS/
    pad_for_full_grid) - a plain Treeview needs neither: it never draws
    a leftover blank spreadsheet grid below its last real row, so blank
    padding rows (all "") are silently skipped in set_sheet_data() below
    rather than shown as empty list rows.

    Row "position" (what tksheet calls a row) is always resolved via
    get_children()[position] / self.index(iid) at call time, never
    baked into the item id - so add/delete/reorder can never leave a
    position pointing at the wrong row the way a fixed iid-equals-
    position scheme would after a delete.
    """

    def __init__(self, parent, columns, col_widths=None, text_columns=(), center_columns=(), style="ERP.Treeview"):
        col_widths = col_widths or {}
        columns = list(columns)
        super().__init__(parent, columns=columns, show="headings", style=style)
        self._cols = columns
        for col in columns:
            # + CENTER_PAD_WIDTH to match make_excel_sheet()'s own
            # per-column width convention - the three screens using this
            # adapter each compute their own "stretch the last/Medicine
            # column to fill leftover width" arithmetic against
            # col_widths.get(c, 120) + CENTER_PAD_WIDTH per fixed column
            # (see medicine_master.py/purchase.py's own stretch closures,
            # unchanged by this switch); constructing columns any
            # narrower than that would under-count the real fixed width
            # and leave a blank strip on the right - the exact "doesn't
            # fill the screen" complaint that stretch logic exists to fix.
            width = col_widths.get(col, 120) + CENTER_PAD_WIDTH
            if col in text_columns:
                anchor = "w"
            elif col in center_columns:
                anchor = "center"
            else:
                anchor = "e"
            self.heading(col, text=col)
            self.column(col, width=width, anchor=anchor, stretch=False)

        # Same zebra shades tksheet's alternate_color used, so a screen
        # switched to this adapter still looks like the same app.
        self.tag_configure("evenrow", background=ROW_EVEN_BG)
        self.tag_configure("oddrow", background=ROW_ODD_BG)
        self._highlight_tags = {}

        # tksheet's own equivalent of Treeview's native "<<TreeviewSelect>>"
        # is called "<<SheetSelect>>" - every screen using this adapter
        # already binds THAT name (unchanged, since it didn't need to
        # know which grid technology it's running on), so re-fire it
        # here off the real native event instead of asking three files
        # to rename their own bind() calls.
        self.bind("<<TreeviewSelect>>", lambda e: self.event_generate("<<SheetSelect>>"), add=True)

    # ---- construction-time / layout helpers (tksheet API parity) -----

    def enable_bindings(self, *args, **kwargs):
        # single_select/row_select/arrowkeys/column_width_resize are all
        # native ttk.Treeview behaviour already (default selectmode is
        # "browse" = single row; header-border drag resizes a column).
        # copy/sort_columns are NOT native to Treeview - added below,
        # only when the caller actually asked for them, same as tksheet
        # only having them when passed to its own enable_bindings().
        if "sort_columns" in args:
            for col in self._cols:
                self.heading(col, command=lambda c=col: self._sort_by(c))
        if "copy" in args:
            self.bind("<Control-c>", self._copy_selection, add=True)

    def _sort_by(self, col, reverse=False):
        def sort_key(iid):
            v = self.set(iid, col)
            try:
                return (0, float(v))
            except (ValueError, TypeError):
                return (1, str(v).lower())
        children = sorted(self.get_children(), key=sort_key, reverse=reverse)
        for index, iid in enumerate(children):
            self.move(iid, "", index)
        self.heading(col, command=lambda: self._sort_by(col, not reverse))
        self._restripe()

    def _copy_selection(self, event=None):
        sel = self.selection()
        if not sel:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append("\t".join(str(v) for v in self.item(sel[0])["values"]))
        except Exception:
            pass

    def _restripe(self):
        for i, iid in enumerate(self.get_children()):
            keep = [t for t in self.item(iid, "tags") if t not in ("evenrow", "oddrow")]
            keep.append("evenrow" if i % 2 == 0 else "oddrow")
            self.item(iid, tags=tuple(keep))

    def column_width(self, column, width=None, **kwargs):
        col = self._cols[column] if isinstance(column, int) else column
        self.column(col, width=width)

    # ---- data (tksheet API parity) ------------------------------------

    def set_sheet_data(self, data, reset_col_positions=None, reset_row_positions=None, reset_highlights=None):
        self.delete(*self.get_children())
        self._highlight_tags = {}
        n = 0
        for row in data:
            if all((v == "" or v is None) for v in row):
                continue  # tksheet-only cosmetic padding row (pad_for_full_grid) - a plain list draws fine with none
            tag = "evenrow" if n % 2 == 0 else "oddrow"
            self.insert("", "end", values=list(row), tags=(tag,))
            n += 1

    def get_sheet_data(self):
        return [list(self.item(iid)["values"]) for iid in self.get_children()]

    def insert_row(self, values, idx=None, redraw=True):
        # Not called directly by any of the three screens using this
        # adapter today (only ui_style.SheetTreeAdapter above calls it,
        # on a real tksheet.Sheet) - included for API completeness.
        pos = "end" if idx is None else idx
        tag = "evenrow" if (idx or 0) % 2 == 0 else "oddrow"
        self.insert("", pos, values=list(values), tags=(tag,))

    def del_rows(self, rows):
        if isinstance(rows, int):
            rows = [rows]
        children = self.get_children()
        for iid in [children[r] for r in sorted(set(rows)) if 0 <= r < len(children)]:
            self.delete(iid)
        self._restripe()

    # ---- selection / highlight (tksheet API parity) -------------------

    def get_currently_selected(self):
        sel = self.selection()
        if not sel:
            return None
        return _SelectedCell(self.index(sel[0]))

    def get_selected_rows(self):
        return {self.index(iid) for iid in self.selection()}

    def _tag_for(self, bg, fg):
        key = (bg, fg)
        tag = self._highlight_tags.get(key)
        if tag is None:
            tag = f"hl{len(self._highlight_tags)}"
            self.tag_configure(tag, background=bg, foreground=fg)
            self._highlight_tags[key] = tag
        return tag

    def _iid_at(self, row):
        children = self.get_children()
        return children[row] if 0 <= row < len(children) else None

    def highlight_rows(self, rows, bg=None, fg=None, redraw=True):
        if isinstance(rows, int):
            rows = [rows]
        tag = self._tag_for(bg, fg)
        for r in rows:
            iid = self._iid_at(r)
            if iid is not None:
                self.item(iid, tags=(tag,))

    def dehighlight_rows(self, rows):
        if isinstance(rows, int):
            rows = [rows]
        for r in rows:
            iid = self._iid_at(r)
            if iid is not None:
                self.item(iid, tags=("evenrow" if r % 2 == 0 else "oddrow",))

    def dehighlight_all(self):
        self._restripe()

    # ---- row-data / dynamic-columns (tksheet API parity, Aug 2026) ---
    # Added when Bulk Import's review grid and Purchase Item Summary's
    # item/date-view toggle were switched to this adapter too - these
    # two screens use a few tksheet calls the original three (Medicine
    # Master/Brand Master/Purchase) never needed.

    def get_row_data(self, row_idx):
        """tksheet's per-row values getter, by position. Used by Bulk
        Import's "Edit Selected Row"/"Force New Item" to read a row's
        current values into an edit dialog."""
        children = self.get_children()
        if not (0 <= row_idx < len(children)):
            return []
        return list(self.item(children[row_idx])["values"])

    def set_row_data(self, row_idx, values):
        """tksheet's per-row values setter, by position - overwrites a
        row in place (Bulk Import's edit dialog saving back onto the
        same row) without disturbing its zebra/highlight tag."""
        children = self.get_children()
        if 0 <= row_idx < len(children):
            self.item(children[row_idx], values=list(values))

    def highlight_cells(self, row, column, bg=None, fg=None, **kwargs):
        """tksheet lets a single CELL get its own color, independent of
        the rest of its row (Bulk Import flags one bad field, e.g. a
        blank Batch, without tinting the whole row). A ttk.Treeview has
        no per-cell background - only per-row, via tags - so this is an
        intentional approximation: it colors the whole row the cell's
        color instead of just that cell. Slightly less precise than
        tksheet, but the row is still visibly flagged, which is the
        actual point; `column` is accepted for call-signature
        compatibility and otherwise unused."""
        self.highlight_rows(rows=[row], bg=bg, fg=fg)

    def headers(self, new_headers, **kwargs):
        """tksheet's bulk column-rename/reconfigure - Purchase Item
        Summary uses this to swap the grid between its "by item" and
        "by date" views, which genuinely have a different column COUNT
        and identity, not just different labels (unlike every other
        screen in this app, which never changes its own columns at
        runtime); Reports' SheetTreeAdapter.__setitem__ also calls this
        (with reset_col_positions=True) every time a report switches
        layout. Rebuilds the Treeview's column set from scratch - since
        that's always a full rebuild here, reset_col_positions and any
        other tksheet-only kwarg is accepted and silently ignored
        rather than raising. The caller always follows this with its
        own column_width()/align_columns() calls per column, so the
        placeholder width/anchor set here just needs to not crash
        before those run."""
        new_headers = list(new_headers)
        self._cols = new_headers
        self["columns"] = new_headers
        for col in new_headers:
            self.heading(col, text=col)
            self.column(col, width=100, anchor="w", stretch=False)

    def align_columns(self, columns, align, align_header=False, **kwargs):
        """tksheet's per-column text/header alignment, settable AFTER
        construction (unlike the fixed text_columns=/center_columns=
        given to make_plain_sheet() at build time) - needed alongside
        headers() above since the alignment that's right for one view's
        columns is wrong for the other's. `columns` is a list of column
        POSITIONS (tksheet convention), matching how column_width()
        above already takes an index."""
        for i in columns:
            col = self._cols[i] if isinstance(i, int) else i
            self.column(col, anchor=align)
            if align_header:
                self.heading(col, anchor=align)


def make_plain_sheet(parent, columns, col_widths=None, text_columns=(), center_columns=()):
    """
    Drop-in replacement for make_excel_sheet() above, same call
    signature, returning a PlainSheet (real ttk.Treeview) instead of a
    tksheet.Sheet - see that class's docstring for why. Callers pack
    the result with fill="both" (unlike make_excel_sheet's fill="y" -
    a Treeview has no fixed "exact content width" to preserve, it's
    meant to stretch).
    """
    return PlainSheet(
        parent, columns, col_widths=col_widths,
        text_columns=text_columns, center_columns=center_columns,
    )


# Minimum row count set_sheet_data() is padded up to by pad_for_full_grid()
# below - picked to comfortably fill a table area under a title+search bar
# on a maximized 1920x1080 window at tksheet's default ~26-28px row height
# (matches ROW_HEIGHT above), without being so tall it forces a scrollbar
# on a smaller laptop screen for screens that already have this many rows
# of real data anyway.
MIN_VISIBLE_ROWS = 28


def pad_for_full_grid(data, num_columns, min_rows=MIN_VISIBLE_ROWS):
    """
    Pads a tksheet `data` list (a list of row-lists, as built by every
    screen's _render_*_rows()/load_*() before calling set_sheet_data())
    with blank rows up to `min_rows` total.

    Why: tksheet only draws grid lines, borders and zebra striping for
    as many rows as are actually IN `data` - it does NOT keep drawing
    an empty spreadsheet grid below that like Excel does. A screen with
    few real rows (a fresh install's Medicine Master with 14 items, for
    example) visibly stops after row 14 and shows a large plain-white,
    grid-less gap filling the rest of the window - confirmed with a
    headless screenshot test during the Aug 2026 "screens don't fill
    the window / look like a letterboxed video" pass (this is a
    DIFFERENT axis of the same complaint MAX_STRETCH_COLUMN_WIDTH above
    already addresses horizontally - this one is vertical). Padding
    with blank rows makes the grid keep drawing all the way down,
    matching how a real spreadsheet always looks "full" regardless of
    how much data is actually in it.

    Purely cosmetic - every caller's row-click handler (Stock's
    on_row_select, Brand Master's equivalent, etc.) already has to
    guard `current.row >= len(real_row_list)` for the ordinary
    zero-rows case, so clicking a padding row safely falls into that
    same "nothing selected" branch instead of erroring. Real row
    indices (used for highlight_rows() on low-stock/expired rows, and
    for mapping a clicked row back to a medicine/brand name) are
    unaffected since padding rows are appended AFTER the real data,
    never inserted before or between real rows.
    """
    pad_needed = min_rows - len(data)
    if pad_needed <= 0:
        return data
    return data + [[""] * num_columns for _ in range(pad_needed)]


def clean_row(row):
    """
    Returns `row` (any sequence - a plain DB tuple is the usual case)
    with every None replaced by "". Use at every ttk.Treeview/tksheet
    insert site that passes a raw DB row straight through as
    values=row (or into a tksheet data list) - without this, a NULL
    database column renders as the literal text "None" in the UI
    (first spotted in Supplier Master's Contact Person/Mobile/City
    columns, which are optional fields many suppliers were saved
    without) - unprofessional-looking, and easy to mistake for a real
    saved value instead of "nothing on file". Only touches actual
    None - a real 0 or "" already displays fine and is left as-is.
    """
    return tuple("" if v is None else v for v in row)


# Bindings every read-only sheet in the app enables - selection, copy,
# resize, sort - with "edit_cell"/"paste"/"cut"/"delete"/"undo"
# deliberately left out. Unlike Treeview, tksheet cells are directly
# editable by default; a stray double-click typing into a report table
# must not look like it changed something, since it would only change
# the on-screen text, not the database, until the next refresh silently
# reverted it. Screens that DO need in-grid editing (Bulk Import's
# review grid) pass their own binding list instead of this one.
READONLY_BINDINGS = (
    "single_select", "row_select", "arrowkeys",
    "column_width_resize", "row_height_resize",
    "copy", "sort_columns",
)


def enable_row_highlight_on_select(sheet):
    """
    Makes clicking ANY cell in a row highlight the WHOLE row in the
    app's selection colour (SELECT_BG/SELECT_FG), matching how
    ttk.Treeview always highlighted an entire row on click.

    tksheet's own default is cell-only selection - clicking a data cell
    (e.g. the "Company" column) puts a blue border around just that one
    cell, leaving the rest of the row looking unselected, which reads as
    broken/inconsistent next to every other screen that used to be a
    Treeview. Since a table built via make_excel_sheet() has
    show_row_index=False (no separate row-number gutter to click for
    Treeview-style row selection), this re-creates that whole-row
    behaviour by watching every selection change and re-painting the
    selected row manually.

    Safe to call on a screen that already has its own "<<SheetSelect>>"
    handler (e.g. medicine_master.py's select_record(), which populates
    the edit form from the clicked row) - uses add=True so this is
    layered ON TOP of that binding, not a replacement for it.

    Deliberately NOT wired into every table automatically (e.g. NOT used
    for Smart Alerts' Expiry/Distributor Return tabs) - those already
    colour rows red/yellow for expired/near-expiry status via
    tag_configure()+highlight_rows(), and painting the clicked row blue
    would overwrite (tksheet highlight is one colour per row, not
    layered) that more important safety-relevant colour for as long as
    it's selected.
    """
    state = {"row": None}

    def _on_select(event=None):
        current = sheet.get_currently_selected()
        if not current or current.row is None:
            return
        if state["row"] is not None and state["row"] != current.row:
            sheet.dehighlight_rows([state["row"]])
        sheet.highlight_rows([current.row], bg=SELECT_BG, fg=SELECT_FG)
        state["row"] = current.row

    sheet.bind("<<SheetSelect>>", _on_select, add=True)


class SheetTreeAdapter:
    """
    Minimal ttk.Treeview-compatible facade over a tksheet.Sheet (built
    with make_excel_sheet() above), so a screen written against
    ttk.Treeview's API can switch to the Excel-grid look without
    rewriting every method that touches the table. Covers the subset of
    Treeview's API actually used across the app's report/list screens:
    get_children()/delete()/insert() to (re)populate rows,
    heading()/column() as no-ops (tksheet's make_excel_sheet() already
    set real widths/alignment at construction time), ["columns"] = cols
    to re-head a table whose column set changes at runtime (Reports
    switches between a dozen different report layouts), and
    tag_configure()/selection()/item() for screens that color-code rows
    (Smart Alerts' expired/expiring-soon highlighting) and act on
    whichever row the user clicked (Smart Alerts' "Create Return for
    Selected").

    Deliberately does NOT implement .bind()/.focus()/.set() or any
    editing-related Treeview method - no screen using this adapter needs
    them (verified by grep before each screen was migrated); a screen
    that later needs one of those would need this class extended first,
    not silently ignored.
    """

    def __init__(self, sheet, columns=None, col_widths=None, stretch=True):
        self._sheet = sheet
        self._rows = []   # value-tuples, index = row id (also the tksheet row index)
        self._tag_styles = {}  # tag name -> (bg, fg)
        self._cols = list(columns) if columns else []
        self._col_widths = dict(col_widths) if col_widths else {}
        # Stretch the LAST column to soak up whatever width is left over
        # once the widget is packed with fill="both" (like every other
        # Excel-grid screen - Medicine Master, Stock) and turns out wider
        # than the sum of its own columns. Without this, that leftover
        # space showed as a plain colored block trailing past the last
        # real column.
        #
        # stretch=False (Smart Alerts passes this) skips binding
        # <Configure> entirely - Smart Alerts' tables live inside a
        # ttk.Notebook tab, unlike Reports' plain Frame, and a live
        # per-resize column_width() call in there was the leading
        # suspect for a silent freeze/blank screen (Notebook children
        # can react to a size-changing call by re-firing <Configure> on
        # themselves, and even with a same-width guard the two widgets'
        # geometry negotiation could still be fighting each other in a
        # way this sandbox has no way to reproduce or rule out without
        # tkinter). Smart Alerts' tables are built once and never change
        # columns afterward, so make_excel_sheet()'s one-time
        # construction-time width (sheet_total_width()) plus packing
        # with fill="y" instead of "both" is enough - narrower than the
        # window, but proven to actually render, which matters more than
        # the cosmetic full-width stretch while this is unresolved.
        self._last_applied_width = None
        if stretch:
            self._sheet.bind("<Configure>", self._on_configure)

    def _on_configure(self, event=None):
        self._apply_stretch(event.width if event is not None else None)

    def _apply_stretch(self, widget_width=None):
        if not self._cols:
            return
        if widget_width is None:
            widget_width = self._sheet.winfo_width()
        if widget_width <= 1:
            return  # widget not yet realized/laid out - <Configure> will fire again once it is
        fixed = sum(
            self._col_widths.get(c, 120) + CENTER_PAD_WIDTH for c in self._cols[:-1]
        )
        last_width = max(120 + CENTER_PAD_WIDTH, widget_width - fixed - _SCROLLBAR_ALLOWANCE)
        # Guard against reapplying the same width - CRITICAL when this
        # sheet lives inside a ttk.Notebook tab (Smart Alerts), not just
        # a plain Frame (Reports). column_width() can itself cause the
        # sheet's canvas to re-request its size from Tk, which a
        # Notebook (unlike a plain Frame) may respond to by re-firing
        # <Configure> on its child - without this guard that becomes
        # Configure -> column_width() -> Configure -> column_width()...
        # forever, an infinite loop with no Python exception and nothing
        # printed to the terminal, just a permanently blank/frozen
        # screen. This was very likely why Smart Alerts (Notebook-based)
        # broke while Reports (plain Frame, same adapter) didn't.
        if last_width == self._last_applied_width:
            return
        self._last_applied_width = last_width
        self._sheet.column_width(column=len(self._cols) - 1, width=last_width)

    def get_children(self):
        return list(range(len(self._rows)))

    def delete(self, *ids):
        # Every caller in this app always does delete(*get_children())
        # to clear everything before a fresh load - there's no
        # partial-delete use case here (verified by grep), so this
        # always clears fully regardless of which ids were passed.
        self._rows = []
        self._sheet.set_sheet_data([], reset_col_positions=False, reset_row_positions=True)
        self._sheet.dehighlight_all()

    def insert(self, parent, index, values, tags=()):
        row_id = len(self._rows)
        values = tuple(values)
        self._rows.append(values)
        # insert_row() (a real single-row insert) rather than rebuilding
        # the whole table via set_sheet_data() on every call - the
        # latter would be an O(rows^2) rebuild for any table with more
        # than a handful of lines.
        self._sheet.insert_row(list(values), idx=row_id, redraw=True)
        tag = tags[0] if tags else None
        style = self._tag_styles.get(tag) if tag else None
        if style:
            bg, fg = style
            self._sheet.highlight_rows([row_id], bg=bg, fg=fg, redraw=False)
        return row_id

    def tag_configure(self, tag, background=None, foreground=None):
        self._tag_styles[tag] = (background, foreground)

    def heading(self, col, text=None):
        pass  # header text is set via .headers() in __setitem__ below

    def column(self, col, width=None, anchor=None):
        pass  # widths/alignment are set once, at construction, by make_excel_sheet()

    def __setitem__(self, key, value):
        if key != "columns":
            return
        cols = list(value)
        # reset_col_positions=True is required here, not optional -
        # without it tksheet keeps whatever column COUNT the widest
        # previous report left behind (headers() only replaces the
        # header TEXT list, not the underlying column/width count) and
        # any leftover columns beyond the new, shorter header list fall
        # back to tksheet's default spreadsheet letters (A, B, C...),
        # which is exactly the "stray G/H/I columns + a blank blue
        # header block trailing off the right edge" bug reported after
        # switching from a 9-column report (Prescription Register) to a
        # 6-column one (Stock). This call must always run right after
        # delete() has already cleared the sheet's data to empty (see
        # Reports.update_table_headers()) so total_data_cols() has
        # nothing but the new header list to size the table from -
        # calling this before clearing old data could size the table
        # against the previous report's still-present row data instead.
        self._sheet.headers(cols, reset_col_positions=True)
        # A table whose column set changes at runtime (Reports) can't
        # know per-report which columns are text/number, so every
        # column left-aligns on a header change - a table built once
        # with a fixed column set (Smart Alerts) never hits this path,
        # its make_excel_sheet() alignment from construction stands.
        for i in range(len(cols)):
            self._sheet.align_columns(columns=[i], align="w", align_header=True)
        # reset_col_positions() (just above, via headers()) resets every
        # column to tksheet's bare default_column_width (120px, no
        # padding) - re-apply the same +CENTER_PAD_WIDTH padding
        # make_excel_sheet() used at construction so a report with a
        # short column list doesn't look cramped compared to one that
        # was on screen first.
        width_per_col = 120 + CENTER_PAD_WIDTH
        for i in range(len(cols)):
            self._sheet.column_width(column=i, width=width_per_col)
        # Record the new column set and immediately re-stretch the last
        # column against the widget's CURRENT on-screen width - unlike
        # at first construction, by the time a report switch happens the
        # widget has already been laid out at least once, so
        # winfo_width() returns its real size rather than 1, and a fresh
        # <Configure> event won't necessarily fire just because the
        # columns changed (the widget's own outer size didn't).
        self._cols = cols
        self._col_widths = {}
        # Force a real re-check even if the new report's computed last-
        # column width happens to numerically match the previous
        # report's (possible with same column count) - the guard in
        # _apply_stretch() only protects against reapplying an UNCHANGED
        # width, not against skipping a genuinely new column set.
        self._last_applied_width = None
        self._apply_stretch()

    def selection(self):
        return tuple(self._sheet.get_selected_rows())

    def item(self, row_id):
        if 0 <= row_id < len(self._rows):
            return {"values": list(self._rows[row_id])}
        return {"values": []}


# ---------------------------------------------------------------------
# Keyboard-shortcut footer bar + Export/Print helpers (2026-08-22)
#
# User asked for BharatERP-style features after seeing that competitor's
# screenshots: a Classic-Blue footer bar showing the active keyboard
# shortcuts (this was "Mockup B" from an earlier round in this same
# session - a static sample image, now approved for real implementation),
# plus a real Export-to-Excel flow with a Location/File Name picker
# (matching BharatERP's own Export dialog) instead of whatever a screen
# already did, and a Print action. Built here once so Medicine Master,
# Brand Master, and Stock Management all get an identical-looking,
# identically-behaving footer instead of three separate implementations.
# ---------------------------------------------------------------------

FOOTER_BG = "#1565C0"
FOOTER_FG = "white"
FOOTER_FONT = ("Segoe UI", 9, "bold")


def make_shortcut_footer(parent, shortcuts, on_print=None, on_export=None):
    """
    Builds (but does NOT pack) a Classic-Blue footer bar: shortcut hints
    on the left, PRINT/EXPORT buttons on the right. Caller packs the
    returned Frame with side="bottom", fill="x" - AFTER the main table/
    form content has already been packed, so it docks at the very
    bottom of the screen regardless of pack() call order (Tk resolves
    side="bottom" widgets independently of when they were added).

    `shortcuts` is an ordered list of (key_label, action_label) tuples,
    e.g. [("ENTER", "Edit Row"), ("DEL", "Delete"), ("CTRL+S", "Save"),
    ("F3", "Search"), ("ESC", "Clear")] - matches Mockup B's design.
    Only the shortcuts that make sense for a given screen should be
    passed (Stock Management has no Save/Delete/Edit-Row concept of its
    own, so it only gets F3=Search here).

    `on_print`/`on_export`, if given, add a PRINT/EXPORT button that
    calls back into the screen's own print_action()/export_action() -
    kept as plain callbacks (not baked into this helper) since what
    "print" and "export" mean differs per screen (different columns,
    different row source).
    """
    footer = tk.Frame(parent, bg=FOOTER_BG, height=34)
    footer.pack_propagate(False)

    left = tk.Frame(footer, bg=FOOTER_BG)
    left.pack(side="left", padx=12)

    for key_label, action_label in shortcuts:
        tk.Label(
            left, text=f"{key_label} = {action_label}",
            bg=FOOTER_BG, fg=FOOTER_FG, font=FOOTER_FONT,
        ).pack(side="left", padx=(0, 18))

    if on_print is not None or on_export is not None:
        right = tk.Frame(footer, bg=FOOTER_BG)
        right.pack(side="right", padx=12)

        # Packed EXPORT-then-PRINT with side="right" so PRINT ends up
        # the leftmost of the two, matching BharatERP's own
        # "PRINT [CTRL+P]  EXPORT [CTRL+E]" left-to-right order.
        if on_export is not None:
            tk.Button(
                right, text="EXPORT", bg="white", fg=FOOTER_BG,
                font=FOOTER_FONT, width=10, cursor="hand2",
                command=on_export,
            ).pack(side="right", padx=(8, 0))
        if on_print is not None:
            tk.Button(
                right, text="PRINT", bg="white", fg=FOOTER_BG,
                font=FOOTER_FONT, width=10, cursor="hand2",
                command=on_print,
            ).pack(side="right", padx=(8, 0))

    return footer


def export_rows_to_excel(parent, headers, rows, default_filename="export"):
    """
    BharatERP-style "Export to Excel" flow: a real save-file dialog
    (Windows' native one, which already has the Location-folder-picker
    + File Name field BharatERP's own custom popup was recreating) then
    writes a real .xlsx via openpyxl - not a screen-specific CSV dump.

    `rows` must already be display-ready (strings/numbers, no raw SQL
    NULLs - callers should clean_row() each row first, same as the grid
    itself does, so a blank DB field exports as "" not the string
    "None"). Returns True on a completed export, False if the user
    cancelled the dialog or the write failed (a message box already
    explains which, either way - callers don't need to show their own).
    """
    # Local import (not module-level) - ui_popups.py itself imports
    # ui_style for center_window(), so a top-level "import ui_popups"
    # here would be circular. A function-scoped import is the standard
    # safe way to break that: by the time this function actually runs,
    # both modules have already finished loading.
    import ui_popups

    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Export to Excel",
        defaultextension=".xlsx",
        filetypes=[("Excel Workbook", "*.xlsx")],
        initialfile=default_filename,
    )
    if not path:
        return False

    try:
        import openpyxl
    except ImportError:
        ui_popups.show_error(
            parent,
            "Export Failed",
            "The 'openpyxl' package is required for Excel export but "
            "isn't installed. Run: pip install openpyxl"
        )
        return False

    try:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(list(headers))
        for row in rows:
            ws.append(list(row))
        for i, header in enumerate(headers, start=1):
            # Rough auto-width so the exported file isn't all-#### /
            # squeezed columns on first open - based on the header and
            # longest cell text in that column, capped so one very long
            # medicine name doesn't blow a column out to full-screen.
            longest = len(str(header))
            for row in rows:
                if i - 1 < len(row):
                    longest = max(longest, len(str(row[i - 1])))
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = min(longest + 2, 45)
        wb.save(path)
    except Exception as e:
        ui_popups.show_error(parent, "Export Failed", str(e))
        return False

    ui_popups.show_info(parent, "Export", f"Exported successfully to:\n{path}")
    return True


# ---------------------------------------------------------------------
# ERP-wide Keyboard Navigation + Dual Input Support (Aug 2026)
#
# User's explicit ask: every search/select control across the whole app
# (Billing, Purchase Entry, Purchase Order, Stock Adjustment, Expiry
# Return, Purchase Return, Customer/Supplier Ledger, WhatsApp, and every
# Master/browse screen - Medicine Master, Brand Master, Stock, Customer,
# Supplier) should support ArrowUp/ArrowDown + Enter/Tab to select, PLUS
# full mouse (hover/click), with selecting an item auto-focusing the
# next logical field, and Enter on the last field of a row adding it and
# returning focus to the top search box - all WITHOUT breaking whatever
# mouse behaviour a screen already had. Explicitly done as ONE pass
# across every screen at once (user's own choice, over a phased
# rollout), so this pair of helpers is the single shared implementation
# every screen wires into rather than each screen growing its own copy.
#
# A survey of every screen's existing search/select widget (before this
# pass) found two distinct UI families, each needing its own helper:
#
#   1. "Add item" ttk.Combobox flows (Billing's medicine box, Purchase
#      Entry's supplier/medicine boxes, Purchase Order, Stock
#      Adjustment, Expiry Return, Purchase Return's bill box, the
#      Ledger/WhatsApp lookup boxes) - these already get real
#      ArrowUp/ArrowDown + mouse-click navigation of their dropdown for
#      free from Tk's own ttk::combobox popdown listbox (standard,
#      well-tested Tk behaviour, not something worth re-implementing).
#      What was actually MISSING and inconsistent screen-to-screen was:
#      live filtering-as-you-type on some screens, Enter not confirming/
#      advancing on others, and - the most common gap - a mouse click on
#      a suggestion NOT advancing focus to the next field the way Enter
#      already did on the one or two screens that had any Enter-chain at
#      all (billing.py's medicine box being the clearest example: typing
#      + Enter jumped to Qty, but clicking the same suggestion with the
#      mouse did not). bind_search_combo() below is the fix: it routes
#      typing, Enter, Tab-away and mouse-click through the exact same
#      "confirm, then advance if valid" path, so every input method
#      behaves identically.
#
#   2. "Search box above a results grid" flows (Medicine Master, Brand
#      Master, Stock, Customer, Supplier) - a live-filtered tk.Entry
#      (via StringVar.trace_add("write", ...), not a Combobox) sits
#      above a tksheet.Sheet or ttk.Treeview grid that already has full
#      native arrow-key navigation once it HAS keyboard focus, and a
#      working mouse-click-to-select-row handler. The one gap on every
#      one of these screens: nothing moved keyboard focus from the
#      search box INTO the grid - a person who typed a search term had
#      to reach for the mouse to open the (often single, already-
#      narrowed-down) result. bind_search_to_grid() below closes that
#      one gap without touching anything else - existing mouse clicks,
#      existing Enter/Delete/Ctrl+S bindings already on the grid itself,
#      and existing search-filtering logic are all left completely
#      alone.
# ---------------------------------------------------------------------

# Keys that must NOT re-trigger a live-filter re-query while the user is
# navigating/confirming rather than typing a new search term - typing
# these into an already-open dropdown would otherwise wipe out the very
# list the user is trying to arrow through.
_NAV_KEYS = (
    "Up", "Down", "Left", "Right", "Return", "KP_Enter", "Escape", "Tab",
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
)


def bind_search_combo(combo, on_filter=None, on_confirm=None, next_widget=None):
    """
    Wires a ttk.Combobox search box (state="normal") to behave the same
    way on EVERY screen: live filter as you type, Tk's own native
    dropdown for ArrowUp/ArrowDown + mouse hover/click (nothing to add
    there - ttk already does this), and - the actual gap this closes -
    Enter, Tab-away, AND a mouse click on a suggestion all running the
    exact same "resolve the typed/picked value, then move on" logic,
    instead of only Enter doing so like most screens had before.

    on_filter(typed_text) -> None
        Called on every real keystroke (navigation/modifier keys are
        filtered out automatically - see _NAV_KEYS) with the box's
        current text. Caller's job, unchanged from before this helper:
        set combo["values"] to whatever should show in the dropdown
        (an in-memory filter, a fresh DB query, or the full list again
        when `typed_text` is empty - exactly what each screen's own
        pre-existing search_medicine()/on_medicine_keyrelease()-style
        method already did). Pass None to skip live filtering entirely
        for a screen whose dropdown list never changes (e.g. a plain
        pick-one-of-a-fixed-list box).

    on_confirm(event) -> bool | None
        Called once a value should be resolved - on Return, on Tab/
        click-away (FocusOut), and on picking an item from the dropdown
        (<<ComboboxSelected>>, fired by BOTH keyboard-Enter-in-the-
        popdown and a mouse click on a suggestion). Caller's job:
        look the typed/selected text up and fill whatever sibling
        fields depend on it (batch/price/address/etc.) - exactly what
        each screen's own get_medicine()/fetch_medicine()-style method
        already did. Return True (or nothing at all - a bare `None` is
        treated as success, matching every existing method here that
        never explicitly returned a value) if the value resolved to a
        real, usable match; return False for an empty/unrecognized
        entry, so a mistyped or blank search box does NOT shove focus
        forward to a field that has nothing valid behind it.

    next_widget
        The widget to focus (and select-all the text of, if it supports
        select_range) once on_confirm succeeds - Qty for an "add item"
        row, or None for a lookup box that should just resolve in place
        with no further field to advance to (e.g. a ledger's customer
        picker).

    Safe to call even when a screen already has its own extra bindings
    on the same combobox for something unrelated (barcode scan, etc.) -
    this only ever ADDS bindings (add="+"), never replaces one.
    """
    def _filter(event):
        if event.keysym in _NAV_KEYS or on_filter is None:
            return
        on_filter(combo.get())

    def _confirm_and_advance(event=None):
        ok = True
        if on_confirm is not None:
            result = on_confirm(event)
            ok = True if result is None else bool(result)
        if ok and next_widget is not None:
            next_widget.focus_set()
            select_range = getattr(next_widget, "select_range", None)
            if select_range is not None:
                try:
                    select_range(0, tk.END)
                except Exception:
                    pass
        if event is not None and event.keysym in ("Return", "KP_Enter"):
            return "break"

    combo.bind("<KeyRelease>", _filter, add="+")
    combo.bind("<Return>", _confirm_and_advance, add="+")
    combo.bind("<KP_Enter>", _confirm_and_advance, add="+")
    combo.bind("<<ComboboxSelected>>", _confirm_and_advance, add="+")
    if on_confirm is not None:
        combo.bind("<FocusOut>", lambda e: on_confirm(e), add="+")


def bind_listbox_navigation(entry, listbox):
    """
    Lets ArrowUp/ArrowDown move the highlighted suggestion in `listbox`
    while `entry` keeps keyboard focus - so the user can keep typing to
    refine a search without ever needing to click into the list first.
    Closes the one keyboard gap left in this app's original "search
    Entry + suggestion Listbox" pattern (clinic_visit.py's patient/
    medicine pickers - the very first screen this whole Aug 2026
    keyboard-nav pass was modeled on, before spreading to every other
    screen): mouse click and Enter-to-confirm already worked without any
    help from here (a plain tk.Listbox's own click handling sets
    curselection() with no custom bind needed, and each screen's own
    Enter/pick handler already reads curselection() - see
    clinic_visit.py's add_item()/_pick_patient()); only Up/Down
    navigation while the Entry (not the Listbox) has focus was missing.

    Deliberately just moves the highlight (via selection_set(), which
    does NOT fire "<<ListboxSelect>>" - a plain Tk fact, not a bug) -
    it does not itself call any pick/confirm method, since screens
    differ in when they resolve a selection (medicine adds a row
    straight on Enter; patient needs its own <Return> wired to actually
    pick, since previously nothing was). Callers wire their own
    Enter -> pick-method binding as needed, same as they already do.
    """
    def _move(delta):
        size = listbox.size()
        if size == 0:
            return
        cur = listbox.curselection()
        idx = cur[0] + delta if cur else (0 if delta > 0 else size - 1)
        idx = max(0, min(size - 1, idx))
        listbox.selection_clear(0, "end")
        listbox.selection_set(idx)
        listbox.activate(idx)
        listbox.see(idx)
        return "break"

    entry.bind("<Down>", lambda e: _move(1), add="+")
    entry.bind("<Up>", lambda e: _move(-1), add="+")


def bind_search_to_grid(search_widget, grid, row_count_fn=None):
    """
    Closes the one keyboard gap every "search box above a results grid"
    Master/browse screen shared (Medicine Master, Brand Master, Stock,
    Customer, Supplier - see this section's header comment): pressing
    Down or Enter while typing in `search_widget` moves real keyboard
    focus into `grid` and selects its first row - firing the exact same
    row-selected handler a mouse click already does (so it loads into
    the edit form / info popup exactly as before), then leaving the
    grid's OWN existing native arrow-key navigation (every grid in this
    app already enables it - see READONLY_BINDINGS above) to take over
    from there. Mouse clicks on any row keep working completely
    unchanged - this never touches click handling.

    Auto-detects which of this app's two grid technologies `grid` is -
    a tksheet.Sheet (Medicine Master/Brand Master/Stock) or a
    ttk.Treeview (Customer/Supplier) - so every screen calls this the
    same one-line way regardless of which it uses.

    row_count_fn: optional callable returning how many REAL data rows
    are currently shown. Required for any tksheet-based screen that
    pads its grid with blank filler rows via pad_for_full_grid() above
    (Medicine Master, Brand Master, Stock all do) - without it, jumping
    into the grid on a zero-result search would "select" a blank
    padding row instead of doing nothing. Pass the screen's own
    position -> real-record-id list length (e.g. `lambda:
    len(self._row_ids)`). Not needed for a ttk.Treeview grid (never
    padded - get_children() already reflects only real rows), so
    Customer/Supplier can omit it.
    """
    def _jump_to_grid(event=None):
        if row_count_fn is not None and row_count_fn() < 1:
            return
        if isinstance(grid, ttk.Treeview):
            children = grid.get_children()
            if not children:
                return
            first = children[0]
            grid.selection_set(first)
            grid.focus(first)
            grid.see(first)
            grid.focus_set()
        else:
            # tksheet.Sheet - select_row's run_binding_func=True fires
            # the screen's own "<<SheetSelect>>" handler (e.g. Medicine
            # Master's select_record()), exactly like a real click.
            try:
                if grid.get_total_rows() < 1:
                    return
                grid.select_row(0, redraw=True, run_binding_func=True)
                grid.see(row=0)
                grid.focus_set("table")
            except Exception:
                pass
        return "break"

    search_widget.bind("<Down>", _jump_to_grid, add="+")
    search_widget.bind("<Return>", _jump_to_grid, add="+")


def print_rows_as_report(headers, rows, title, parent=None):
    """
    Simple "Print" action with no dedicated print-layout library in
    this project: writes a formatted HTML table to a temp file and
    opens it in the default browser with a `window.print()` call baked
    into the page itself, so the browser's own native print dialog pops
    up automatically the moment the report finishes loading - same
    "print preview then send to printer/PDF" experience as any normal
    webpage's Print button, letting the user pick their printer, print
    to PDF, adjust paper size, etc.

    FIX (2026-08-22): originally used `os.startfile(path, "print")` to
    ask Windows' shell to print the file directly with no dialog at
    all - reported broken on the user's real machine, where it doesn't
    raise a catchable Python exception (so the AttributeError/OSError
    fallback below never engaged) and instead pops Windows' own "Select
    an app to open this .html file" chooser with no default action -
    because most browsers, Chrome included, never register a shell
    "print" verb for .html files even when they ARE the registered
    default "open" app. The auto-print-on-load HTML trick sidesteps
    that entirely: it only ever relies on the "open" verb, which is
    reliably registered whenever a default browser exists (confirmed
    from the user's own screenshot: "Default app: Google Chrome").
    """
    rows_html = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    header_html = "".join(f"<th>{h}</th>" for h in headers)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 24px; }}
  h2 {{ color: #1565C0; margin-bottom: 4px; }}
  .meta {{ color: #666; margin-bottom: 16px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #9E9E9E; padding: 4px 8px; font-size: 12px; text-align: left; }}
  th {{ background: #1565C0; color: white; }}
  tr:nth-child(even) {{ background: #F5F5F5; }}
  @media print {{ body {{ margin: 0; }} }}
</style>
<script>
  // Auto-open the browser's native print dialog once the report has
  // fully rendered - see the function docstring above for why this
  // replaced the old os.startfile(path, "print") approach.
  window.onload = function () {{ window.print(); }};
</script>
</head>
<body>
  <h2>Life Care Pharmacy - {title}</h2>
  <div class="meta">{len(rows)} record(s)</div>
  <table><thead><tr>{header_html}</tr></thead><tbody>
  {rows_html}
  </tbody></table>
</body></html>"""

    try:
        fd, path = tempfile.mkstemp(suffix=".html", prefix="lifecare_print_")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html)
    except Exception as e:
        # Local import - see export_rows_to_excel() above for why
        # ui_popups can't be imported at module level here (circular:
        # ui_popups.py itself imports ui_style for center_window()).
        import ui_popups
        ui_popups.show_error(parent, "Print Failed", str(e))
        return False

    try:
        os.startfile(path)
    except AttributeError:
        # os.startfile only exists on Windows - on any other platform
        # (this session's own Linux test sandbox included) fall back to
        # the webbrowser module so there's still a usable printable
        # page instead of a silent no-op.
        webbrowser.open(f"file://{path}")

    return True
