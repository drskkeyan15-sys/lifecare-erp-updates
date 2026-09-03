import tkinter as tk
from datetime import datetime, timedelta

import clinic_repository as repo
import theme

# Lazy matplotlib import (Aug 2026, "Clinic Revenue Analytics" - same
# pattern dashboard.py's own Sales Trend chart already uses, see that
# file's comment for why: matplotlib is not otherwise a dependency of
# this ERP, so a machine without it should just skip these two charts
# instead of crashing Clinic Dashboard on open). Kept as its own local
# copy rather than importing dashboard.py's helper - each screen manages
# its own optional-dependency import, same convention as bulk_import.py's
# lazy openpyxl import.
matplotlib = None
FigureCanvasTkAgg = None
Figure = None
MATPLOTLIB_AVAILABLE = False


def _ensure_matplotlib_import():
    global matplotlib, FigureCanvasTkAgg, Figure, MATPLOTLIB_AVAILABLE
    if MATPLOTLIB_AVAILABLE:
        return True
    try:
        import matplotlib as _matplotlib
        _matplotlib.use("TkAgg")
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _FigureCanvasTkAgg
        from matplotlib.figure import Figure as _Figure
        matplotlib = _matplotlib
        FigureCanvasTkAgg = _FigureCanvasTkAgg
        Figure = _Figure
        MATPLOTLIB_AVAILABLE = True
    except Exception:
        MATPLOTLIB_AVAILABLE = False
    return MATPLOTLIB_AVAILABLE


TREND_DAYS = 30          # matches dashboard.py's own "last 30 days" Sales Trend window
DOCTOR_CHART_MAX = 8     # keep the bar chart readable; full list is in Reports -> Doctor-wise Report


class ClinicDashboard:
    """Simple, non-technical-user-friendly summary cards - Today / This
    Month / This Year - per CLINIC_LEDGER_WORKFLOW.md's dashboard
    section ("simple enough for a non-technical user") - plus (Aug 2026,
    "Clinic Revenue Analytics") two charts underneath:
      - Revenue & Patient Trend: day-wise Total Collection + visit
        (patient) count for the last 30 days, dual-axis line chart.
      - Doctor-wise Revenue: a bar chart of this month's Total
        Collection per doctor (top 8 by collection).
    Both reuse clinic_repository functions that already back the
    text-based Net Profit Report / Doctor-wise Report in
    clinic_reports.py (daily_trend()/doctor_report()), so the numbers
    behind these charts always match those reports exactly - no second
    definition of "revenue" or "patient count" was introduced here.
    Neither chart can ever crash this screen: matplotlib absence, zero
    data, and a plotting error are all handled with a plain text label
    in place of the chart, matching dashboard.py's own Sales Trend
    chart's fail-safe convention."""

    def __init__(self, frame, on_close=None):
        self.frame = frame
        self.on_close = on_close
        self.create_ui()
        self.refresh()

    def create_ui(self):
        title = tk.Label(
            self.frame, text="CLINIC LEDGER - DASHBOARD",
            bg=theme.PRIMARY, fg="white", font=("Segoe UI", 18, "bold"), pady=10
        )
        title.pack(fill="x")

        top = tk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=10)
        tk.Button(top, text="Refresh", bg=theme.PRIMARY, fg="white",
                  command=self.refresh).pack(side="left")
        if self.on_close:
            tk.Button(top, text="Close", bg=theme.STATUS_DANGER, fg="white",
                      command=self.on_close).pack(side="right")

        # Today/Month/Year cards - no longer expand to fill all remaining
        # vertical space (that was fine when they were the only content),
        # since the new Trends & Analytics charts below now need room too.
        self.sections = tk.Frame(self.frame)
        self.sections.pack(fill="x", padx=10, pady=(0, 10))
        self.today_frame = self._make_section(self.sections, "Today", 0)
        self.month_frame = self._make_section(self.sections, "This Month", 1)
        self.year_frame = self._make_section(self.sections, "This Year", 2)

        # --- Trends & Analytics (Aug 2026, "Clinic Revenue Analytics") ---
        charts = tk.Frame(self.frame)
        charts.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        charts.grid_columnconfigure(0, weight=1)
        charts.grid_columnconfigure(1, weight=1)
        charts.grid_rowconfigure(0, weight=1)

        trend_box = tk.LabelFrame(
            charts, text=f"Revenue & Patient Trend (Last {TREND_DAYS} Days)",
            font=("Segoe UI", 11, "bold")
        )
        trend_box.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        self.trend_chart_frame = tk.Frame(trend_box, bg=theme.SURFACE_WHITE)
        self.trend_chart_frame.pack(fill="both", expand=True, padx=5, pady=5)

        doctor_box = tk.LabelFrame(
            charts, text="Doctor-wise Revenue (This Month)",
            font=("Segoe UI", 11, "bold")
        )
        doctor_box.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        self.doctor_chart_frame = tk.Frame(doctor_box, bg=theme.SURFACE_WHITE)
        self.doctor_chart_frame.pack(fill="both", expand=True, padx=5, pady=5)

    def _make_section(self, parent, label, col):
        box = tk.LabelFrame(parent, text=label, font=("Segoe UI", 12, "bold"), padx=15, pady=15)
        box.grid(row=0, column=col, padx=10, sticky="nsew")
        parent.grid_columnconfigure(col, weight=1)
        return box

    def _fill_section(self, box, summary):
        for widget in box.winfo_children():
            widget.destroy()
        rows = [
            ("Patients", summary["visits"]),
            ("Unique Patients", summary["unique_patients"]),
            ("Consultation Income", f"₹ {summary['consultation_income']:,.2f}"),
            ("Medicine MRP Value", f"₹ {summary['medicine_mrp_value']:,.2f}"),
            ("Medicine Cost", f"₹ {summary['medicine_purchase_cost']:,.2f}"),
            ("Total Collection", f"₹ {summary['total_collection']:,.2f}"),
            ("Consulting Charge", f"₹ {summary['consulting_charge']:,.2f}"),
            ("Actual Net Profit", f"₹ {summary['actual_net_profit']:,.2f}"),
            ("Medicine Margin Profit", f"₹ {summary['medicine_margin_profit']:,.2f}"),
            ("Expenses", f"₹ {summary['expenses']:,.2f}"),
            ("Net Profit (after Expenses)", f"₹ {summary['net_profit']:,.2f}"),
        ]
        for i, (label, value) in enumerate(rows):
            highlight = label.startswith("Net Profit")
            tk.Label(box, text=label, font=("Segoe UI", 10)).grid(row=i, column=0, sticky="w", pady=2)
            tk.Label(
                box, text=str(value),
                font=("Segoe UI", 10, "bold"),
                fg=theme.STATUS_SUCCESS if highlight else theme.PRIMARY
            ).grid(row=i, column=1, sticky="e", padx=(20, 0), pady=2)

    def refresh(self):
        now = datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        self._fill_section(self.today_frame, repo.daily_report(today_str))
        self._fill_section(self.month_frame, repo.monthly_report(now.year, now.month))
        self._fill_section(self.year_frame, repo.yearly_report(now.year))
        self._draw_trend_chart()
        self._draw_doctor_chart()

    # --------------------------
    # REVENUE & PATIENT TREND CHART (last 30 days)
    # --------------------------

    def _draw_trend_chart(self):
        for widget in self.trend_chart_frame.winfo_children():
            widget.destroy()

        now = datetime.now()
        date_from = (now - timedelta(days=TREND_DAYS - 1)).strftime("%Y-%m-%d 00:00:00")
        date_to = now.strftime("%Y-%m-%d 23:59:59")
        try:
            rows = repo.daily_trend(date_from, date_to)
        except Exception:
            rows = []

        if not rows:
            tk.Label(
                self.trend_chart_frame, text="No clinic visits recorded yet in this period.",
                bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, font=("Segoe UI", 10, "italic")
            ).pack(pady=20)
            return

        if not _ensure_matplotlib_import():
            tk.Label(
                self.trend_chart_frame,
                text="This chart needs the 'matplotlib' package (pip install matplotlib) "
                     "- the Today/Month/Year cards above are unaffected.",
                bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, font=("Segoe UI", 10, "italic"),
                wraplength=380, justify="center"
            ).pack(pady=20)
            return

        try:
            # rows' dates are raw "YYYY-MM-DD" (daily_trend()'s own
            # format). Displayed here as "27-Aug" instead, matching the
            # header clock's "27-Aug-2026" style and the same fix applied
            # to the main Dashboard's Sales Trend chart (Aug 2026, user
            # asked why a chart showed raw "yyy-mm-dd" dates) - year
            # dropped since every point falls within the last 30 days.
            def _display_date(d):
                try:
                    return datetime.strptime(d, "%Y-%m-%d").strftime("%d-%b")
                except (ValueError, TypeError):
                    return d
            dates = [_display_date(d) for d, _, _ in rows]
            visits = [v for _, v, _ in rows]
            revenue = [float(r or 0) for _, _, r in rows]

            fig = Figure(figsize=(5.2, 3.0), dpi=100, facecolor=theme.SURFACE_WHITE)
            ax1 = fig.add_subplot(111)
            ax1.set_facecolor(theme.SURFACE_WHITE)
            ax1.plot(dates, revenue, marker="o", color="#1565C0", linewidth=2, markersize=3, label="Revenue (₹)")
            ax1.set_ylabel("Revenue (₹)", color="#1565C0", fontsize=8)
            ax1.tick_params(axis="y", labelcolor="#1565C0", labelsize=7)
            ax1.tick_params(axis="x", labelrotation=45, labelsize=6)
            ax1.grid(True, alpha=0.25)

            # Twin y-axis: same visual trick dashboard.py could use for a
            # two-metric trend, but the main Sales Trend chart only ever
            # needed one line - this is the first dual-axis chart in the
            # app, kept local here rather than generalized into a shared
            # helper since nothing else needs it yet.
            ax2 = ax1.twinx()
            ax2.plot(dates, visits, marker="s", color="#2E7D32", linewidth=1.5,
                     markersize=3, linestyle="--", label="Patients")
            ax2.set_ylabel("Patients", color="#2E7D32", fontsize=8)
            ax2.tick_params(axis="y", labelcolor="#2E7D32", labelsize=7)

            fig.tight_layout()

            canvas = FigureCanvasTkAgg(fig, master=self.trend_chart_frame)
            canvas.draw()
            canvas.get_tk_widget().configure(bg=theme.SURFACE_WHITE, highlightthickness=0)
            canvas.get_tk_widget().pack(fill="both", expand=True)
        except Exception:
            tk.Label(
                self.trend_chart_frame, text="Trend chart could not be drawn.",
                bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER, font=("Segoe UI", 10, "italic")
            ).pack(pady=20)

    # --------------------------
    # DOCTOR-WISE REVENUE CHART (this month)
    # --------------------------

    def _draw_doctor_chart(self):
        for widget in self.doctor_chart_frame.winfo_children():
            widget.destroy()

        now = datetime.now()
        date_from = f"{now.year:04d}-{now.month:02d}-01 00:00:00"
        date_to = now.strftime("%Y-%m-%d 23:59:59")
        try:
            rows = repo.doctor_report(date_from, date_to)
        except Exception:
            rows = []

        if not rows:
            tk.Label(
                self.doctor_chart_frame, text="No clinic visits recorded yet this month.",
                bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, font=("Segoe UI", 10, "italic")
            ).pack(pady=20)
            return

        if not _ensure_matplotlib_import():
            tk.Label(
                self.doctor_chart_frame,
                text="This chart needs the 'matplotlib' package (pip install matplotlib) "
                     "- see Reports -> Doctor-wise Report for the same numbers as a table.",
                bg=theme.SURFACE_WHITE, fg=theme.TEXT_MUTED, font=("Segoe UI", 10, "italic"),
                wraplength=380, justify="center"
            ).pack(pady=20)
            return

        try:
            # rows are (doctor, visits, consultation, actual_net_profit,
            # total_collection), already sorted by Total Collection DESC
            # by clinic_repository.doctor_report() itself - only the top
            # DOCTOR_CHART_MAX are charted so the bars stay readable even
            # at a clinic with many doctor-name variants; the complete,
            # untruncated list is always available via Reports ->
            # Doctor-wise Report, so nothing is lost, only not charted.
            top_rows = rows[:DOCTOR_CHART_MAX]
            doctors = [(r[0] or "(Not Set)") for r in top_rows]
            collections = [float(r[4] or 0) for r in top_rows]

            fig = Figure(figsize=(5.2, 3.0), dpi=100, facecolor=theme.SURFACE_WHITE)
            ax = fig.add_subplot(111)
            ax.set_facecolor(theme.SURFACE_WHITE)
            bars = ax.bar(doctors, collections, color="#1565C0")
            ax.set_ylabel("Total Collection (₹)", fontsize=8)
            ax.tick_params(axis="x", labelrotation=30, labelsize=7)
            ax.tick_params(axis="y", labelsize=7)
            for bar, val in zip(bars, collections):
                ax.annotate(
                    f"{val:,.0f}", (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                    ha="center", va="bottom", fontsize=6
                )
            if len(rows) > DOCTOR_CHART_MAX:
                ax.set_title(
                    f"Top {DOCTOR_CHART_MAX} of {len(rows)} doctors - full list in Reports",
                    fontsize=7, color=theme.TEXT_MUTED
                )
            fig.tight_layout()

            canvas = FigureCanvasTkAgg(fig, master=self.doctor_chart_frame)
            canvas.draw()
            canvas.get_tk_widget().configure(bg=theme.SURFACE_WHITE, highlightthickness=0)
            canvas.get_tk_widget().pack(fill="both", expand=True)
        except Exception:
            tk.Label(
                self.doctor_chart_frame, text="Doctor-wise chart could not be drawn.",
                bg=theme.SURFACE_WHITE, fg=theme.STATUS_DANGER, font=("Segoe UI", 10, "italic")
            ).pack(pady=20)
