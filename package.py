"""Annual accountant package: one ZIP with a PDF summary, CSV files and the original receipts.

app.py gathers the numbers with the same helpers the dashboard uses (compute_stats and
friends) into a PackageData object. This module only turns that data into files: it never
recomputes day totals, so the package and the screen cannot disagree.
"""
import csv
import hashlib
import io
import os
import posixpath
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BASE_DIR, "static", "fonts")
LOGO_PATH = os.path.join(BASE_DIR, "static", "img", "logo-mark.png")
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
FONT_CJK = os.path.join(FONT_DIR, "IPAGothic.ttf")  # fallback for Japanese file names

DISCLAIMER = ("Nomad Life is a personal organizer, not tax advice. "
              "Residence rules differ by country.")
RULES = [
    "Dates are inclusive: a stay from 1 to 3 March counts 3 days.",
    "Days not covered by any stay count toward your base country once they have passed. "
    "Planned stays count in full; future days without a stay are not counted yet.",
    "A day shared by two stays, such as a travel day, counts toward the stay that started later.",
    "A side trip inside a longer stay counts toward the side trip. When two stays start on "
    "the same day, the shorter one wins; with identical dates, the one added last wins.",
    "The 183 day line means at least 183 days in the base country.",
]
CHUNK = 64 * 1024
# Every entry is deflated: while streaming, zipfile writes sizes after the data (a data
# descriptor), and strict streaming readers (for example Java's ZipInputStream) only accept
# that for deflated entries. Formats that are already compressed use the fastest level.
FAST_EXTENSIONS = {"pdf", "jpg", "jpeg", "png", "heic", "webp"}
# Budgets keep the longest path well under the 260 character Windows limit, even after
# "Extract All" into a Downloads folder.
MAX_CITY, MAX_COUNTRY, MAX_STEM, MAX_KIND = 24, 24, 40, 20
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}


# ---------- data ----------

@dataclass
class Period:
    """Date range the package covers. Only calendar years are exposed today, but everything
    below works on a range so custom tax years can be added later."""
    start: date
    end: date
    label: str

    @property
    def days(self):
        return (self.end - self.start).days + 1


@dataclass
class CountryTotal:
    country: str
    iso: str
    days: int
    share: float
    so_far: int
    is_base: bool


@dataclass
class Stay:
    number: int
    start: str
    end: str
    city: str
    country: str
    iso: str
    calendar_days: int
    counted_days: int
    notes: str
    receipts: int = 0


@dataclass
class Receipt:
    stay_number: int  # 0 for base documents
    kind: str         # human label, for example "Flight ticket"
    name: str         # display name as the user sees it
    ext: str          # real extension of the stored file
    path: str         # absolute path on disk
    uploaded: str     # local date
    size: int         # bytes (on disk when present, recorded size when missing)
    exists: bool
    arcname: str = ""


@dataclass
class PackageData:
    period: Period
    base_city: str
    base_country: str
    base_iso: str
    generated_at: datetime
    app_version: str
    status: str              # "past", "current" or "future"
    as_of: date
    total_days: int
    elapsed: int
    base_days: int
    abroad_days: int
    base_so_far: int
    abroad_so_far: int
    threshold: int
    base_ok: bool
    countries: list = field(default_factory=list)
    stays: list = field(default_factory=list)
    receipts: list = field(default_factory=list)
    include_notes: bool = False
    upcoming_days: int = 0   # future days not covered by a stay, not counted anywhere yet
    holder: str = ""         # whose package this is: the name from Settings, or the email

    @property
    def root(self):
        return f"NomadLife-{self.period.label}-accountant-package"

    @property
    def missing(self):
        return [r for r in self.receipts if not r.exists]


def zip_name(label):
    return f"NomadLife-{label}-accountant-package.zip"


def country_label(name, iso):
    return f"{name} ({iso})" if iso else name


def stay_label(data, number):
    if number == 0:
        return "Base"
    stay = next((s for s in data.stays if s.number == number), None)
    return f"{number:02d} {stay.city}, {stay.country}" if stay else f"{number:02d}"


# ---------- CSV ----------

def csv_safe(value):
    """Neutralize spreadsheet formulas: a cell starting with = + - @ tab or CR would run as a
    formula in Excel or LibreOffice, so it gets a leading apostrophe."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([csv_safe(v) for v in row])
    # BOM so Excel opens UTF-8 correctly (accents, non Latin names).
    return ("﻿" + buf.getvalue()).encode("utf-8")


def timeline_csv(data):
    header = ["start", "end", "city", "country", "iso_code", "calendar_days", "counted_days",
              "receipts"] + (["notes"] if data.include_notes else [])
    rows = []
    for s in data.stays:
        row = [s.start, s.end, s.city, s.country, s.iso, s.calendar_days, s.counted_days,
               s.receipts]
        if data.include_notes:
            row.append(s.notes)
        rows.append(row)
    return _csv(header, rows)


def country_totals_csv(data):
    rows = [[c.country, c.iso, c.days, f"{c.share:.1f}%", c.so_far, "yes" if c.is_base else "no"]
            for c in data.countries]
    return _csv(["country", "iso_code", "days", "share_of_year", "so_far", "is_base"], rows)


# ---------- safe names inside the ZIP ----------

def clean_segment(text, max_len=60, fallback="file", whole=True):
    """One safe path segment: no separators, control or reserved characters, no leading or
    trailing dots, spaces become hyphens. Keeps letters from any script. Windows device names
    (CON, NUL...) are only a problem as a whole segment, so parts of a name skip that check."""
    text = unicodedata.normalize("NFC", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch)[0] != "C")
    text = re.sub(r'[\\/:*?"<>|]', "-", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip(" .-_")
    text = text[:max_len].rstrip(" .-_")
    if not text or set(text) <= {"."}:
        text = fallback
    if whole and text.split(".")[0].upper() in WINDOWS_RESERVED:
        text = "_" + text
    return text


def _slug(label):
    return clean_segment(re.sub(r"[^\w]+", "-", label.lower()), MAX_KIND, "document", False)


def _unique(name, taken):
    """Add -2, -3 before the extension until the name is free (case insensitive, because
    Windows and macOS file systems are)."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    candidate, n = name, 2
    while candidate.casefold() in taken:
        candidate = f"{stem}-{n}.{ext}" if ext else f"{stem}-{n}"
        n += 1
    taken.add(candidate.casefold())
    return candidate


def _check_arcname(root, arcname):
    norm = posixpath.normpath(arcname)
    parts = norm.split("/")
    if norm != arcname or parts[0] != root or ".." in parts or arcname.startswith("/"):
        raise ValueError(f"unsafe path in package: {arcname!r}")
    return arcname


def assign_arcnames(data):
    """Folder per stay (01_2026-02-01_Canggu_Indonesia) plus receipts/base, file names from the
    document type and display name with the real extension."""
    folders = {0: "base"}
    taken_folders = {"base"}
    for s in data.stays:
        folder = "_".join([f"{s.number:02d}", s.start,
                           clean_segment(s.city, MAX_CITY, "city", False),
                           clean_segment(s.country, MAX_COUNTRY, "country", False)])
        folders[s.number] = _unique(folder, taken_folders)
    taken = {}
    for r in data.receipts:
        stem = r.name
        if stem.lower().endswith("." + r.ext):
            stem = stem[: -len(r.ext) - 1]
        file_name = f"{_slug(r.kind)}_{clean_segment(stem, MAX_STEM, 'receipt', False)}.{r.ext}"
        folder = folders.get(r.stay_number, "base")
        file_name = _unique(file_name, taken.setdefault(folder, set()))
        r.arcname = _check_arcname(data.root, f"{data.root}/receipts/{folder}/{file_name}")


# ---------- summary (shared by the PDF and the tests) ----------

def _fmt_size(size):
    if size < 1024:
        return f"{size} B"
    if round(size / 1024) < 1024:  # never "1024 KB"
        return f"{round(size / 1024)} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def summary_blocks(data):
    """The report as a list of (kind, content) blocks. The PDF renders these; tests read them."""
    y = data.period.label
    blocks = [
        ("title", f"Accountant package {y}"),
        ("meta", ([f"Prepared for: {data.holder}"] if data.holder else []) + [
            f"Base: {data.base_city}, {country_label(data.base_country, data.base_iso)}",
            f"Period: {data.period.start.isoformat()} to {data.period.end.isoformat()} "
            f"({data.total_days} days)",
            f"Generated: {data.generated_at.strftime('%Y-%m-%d %H:%M')} (local time)",
            f"Nomad Life v{data.app_version}",
        ]),
    ]
    base_name = country_label(data.base_country, data.base_iso)
    yes_no = "Yes" if data.base_ok else "No"
    full_year = [
        (f"Days in base country, {base_name}", f"{data.base_days} of {data.total_days}"),
        (f"At least {data.threshold} days in base country", yes_no),
        ("Days abroad", str(data.abroad_days)),
    ]
    if data.upcoming_days:
        full_year.append(("Days still to come without a stay", str(data.upcoming_days)))
    if data.status == "past":
        blocks += [("heading", f"Final numbers for {y}"), ("kv", full_year)]
    elif data.status == "current":
        blocks += [
            ("heading", f"So far (as of {data.as_of.isoformat()})"),
            ("kv", [
                ("Days elapsed", f"{data.elapsed} of {data.total_days}"),
                (f"Days in base country, {base_name}", str(data.base_so_far)),
                ("Days abroad", str(data.abroad_so_far)),
            ]),
            ("heading", "Full year with planned trips"),
            ("kv", full_year),
            ("note", "These numbers add the trips you have already planned. Days still to come "
                     "without a stay are not counted toward any country yet."),
        ]
    else:
        blocks += [
            ("note", f"{y} has not started yet. Only the trips you have already planned are "
                     "counted."),
            ("heading", "Full year with planned trips"),
            ("kv", full_year),
        ]

    blocks.append(("heading", "Days per country"))
    so_far = data.status == "current"
    columns = ["Country", "ISO", "Days", "Share"] + (["So far"] if so_far else [])
    rows = []
    for c in data.countries:
        row = [c.country + (" (base)" if c.is_base else ""), c.iso or "-", str(c.days),
               f"{c.share:.1f}%"]
        if so_far:
            row.append(str(c.so_far))
        rows.append(row)
    counted = sum(c.days for c in data.countries)
    rows.append(["Total", "", str(counted), f"{counted * 100 / data.total_days:.1f}%"]
                + ([str(data.elapsed)] if so_far else []))
    blocks.append(("table", {"columns": columns, "rows": rows,
                             "widths": [44, 10, 12, 14] + ([14] if so_far else []),
                             "align": ["LEFT", "CENTER", "RIGHT", "RIGHT"] + (["RIGHT"] if so_far else [])}))

    blocks.append(("heading", "Travel timeline"))
    if data.stays:
        rows = [[f"{s.number:02d}", f"{s.start} to {s.end}", s.city,
                 country_label(s.country, s.iso), str(s.calendar_days),
                 str(s.counted_days) + (" *" if s.counted_days != s.calendar_days else ""),
                 str(s.receipts)] for s in data.stays]
        blocks.append(("table", {"columns": ["#", "Dates", "City", "Country", "Days", "Counted", "Receipts"],
                                 "rows": rows, "widths": [6, 26, 18, 24, 9, 11, 11],
                                 "align": ["CENTER", "LEFT", "LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"]}))
        if any(s.counted_days != s.calendar_days for s in data.stays):
            blocks.append(("note", "* Counted days are lower when a stay shares days with a stay "
                                   "that takes those days (see the rules below)."))
    else:
        blocks.append(("note", "No stays recorded for this year. Every day counts toward the "
                               "base country." if data.status == "past" else
                               "No stays recorded for this year. Days without a stay count "
                               "toward the base country once they have passed."))

    blocks += [("heading", "Rules used for counting"), ("bullets", RULES)]

    blocks.append(("heading", "Receipt index"))
    if data.receipts:
        rows = [[r.arcname.rsplit("/", 1)[-1], stay_label(data, r.stay_number), r.kind,
                 _fmt_size(r.size) + ("" if r.exists else " (missing)")] for r in data.receipts]
        blocks.append(("table", {"columns": ["File (in the stay's folder)", "Stay", "Type", "Size"],
                                 "rows": rows, "widths": [48, 22, 18, 12],
                                 "align": ["LEFT", "LEFT", "LEFT", "RIGHT"]}))
    else:
        blocks.append(("note", "No receipts uploaded for this year."))
    if data.missing:
        blocks.append(("note", f"{len(data.missing)} receipt file(s) could not be found on the "
                               "server and are not in this package. They are listed as missing "
                               "in manifest.csv."))
    return blocks


def summary_text(data):
    out = []
    for kind, content in summary_blocks(data):
        if kind in ("title", "heading", "note"):
            out.append(content)
        elif kind in ("meta", "bullets"):
            out += content
        elif kind == "kv":
            out += [f"{k}: {v}" for k, v in content]
        elif kind == "table":
            out.append(" | ".join(content["columns"]))
            out += [" | ".join(r) for r in content["rows"]]
    return "\n".join(out)


# ---------- PDF ----------

_GLYPHS = {}


def _glyphs(path):
    """Characters a bundled font can draw (cached)."""
    if path not in _GLYPHS:
        from fontTools.ttLib import TTFont
        _GLYPHS[path] = frozenset(TTFont(path, lazy=True).getBestCmap())
    return _GLYPHS[path]


def _needs_cjk(text):
    """The Japanese fallback font is large, so it is only loaded when a name needs it."""
    latin = _glyphs(FONT_REGULAR)
    return any(ord(ch) not in latin for ch in text if ch != "\n")


def _needs_shaping(text):
    """Right to left scripts (Arabic, Hebrew...) must be shaped: joined letters, drawn right
    to left. Only then is the (slower) HarfBuzz shaping turned on."""
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in text)


def pdf_safe(text, cjk=True):
    """Characters no bundled font can draw become "?" in the PDF (the exact names are always
    in the CSV and manifest files)."""
    latin = _glyphs(FONT_REGULAR)
    extra = _glyphs(FONT_CJK) if cjk else frozenset()
    return "".join(ch if ord(ch) in latin or ord(ch) in extra or ch == "\n" else "?"
                   for ch in str(text))


# Total page count placeholder. The default "{nb}" would also replace that text in city or file
# names; NUL is in no bundled font, so pdf_safe() turns it into "?" in user text.
PAGE_COUNT = "\x00nb\x00"
# A table row must fit on one page, so very long free text is shortened in the PDF (the CSV
# files and the manifest keep the full text).
MAX_CELL = 120


def clip_cell(text):
    return text if len(text) <= MAX_CELL else text[:MAX_CELL - 1].rstrip() + "\u2026"


def render_pdf(data):
    from fpdf import FPDF
    from fpdf.fonts import FontFace

    ink, muted, accent, line = (48, 53, 73), (98, 103, 123), (161, 58, 140), (234, 235, 239)

    class SummaryPDF(FPDF):
        def footer(self):
            self.set_y(-14)
            self.set_draw_color(*line)
            self.line(self.l_margin, self.get_y() - 1, self.w - self.r_margin, self.get_y() - 1)
            self.set_font("DejaVu", size=7.5)
            self.set_text_color(*muted)
            self.cell(self.epw - 30, 5, DISCLAIMER)
            self.cell(30, 5, f"Page {self.page_no()} of {PAGE_COUNT}", align="R")

    pdf = SummaryPDF(format="A4")
    pdf.alias_nb_pages(PAGE_COUNT)
    pdf.set_title(f"Nomad Life accountant package {data.period.label}")
    pdf.set_author("Nomad Life")
    pdf.set_creator(f"Nomad Life v{data.app_version}")
    pdf.add_font("DejaVu", "", FONT_REGULAR)
    pdf.add_font("DejaVu", "B", FONT_BOLD)
    cjk = _needs_cjk(summary_text(data))
    if cjk:
        pdf.add_font("IPAGothic", "", FONT_CJK)
        pdf.set_fallback_fonts(["IPAGothic"], exact_match=False)
    if _needs_shaping(summary_text(data)):
        try:
            pdf.set_text_shaping(True)
        except Exception:  # noqa: BLE001 - without uharfbuzz the text stays unshaped
            pass
    safe = lambda value: pdf_safe(value, cjk)
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_page()

    for kind, content in summary_blocks(data):
        if kind == "title":
            if os.path.exists(LOGO_PATH):
                pdf.image(LOGO_PATH, x=15, y=14, h=13)
            pdf.set_xy(28, 14)
            pdf.set_font("DejaVu", "B", 9)
            pdf.set_text_color(*accent)
            pdf.cell(0, 5, "NOMAD LIFE", new_x="LMARGIN", new_y="NEXT")
            pdf.set_x(28)
            pdf.set_font("DejaVu", "B", 18)
            pdf.set_text_color(*ink)
            pdf.cell(0, 9, safe(content), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        elif kind == "meta":
            pdf.set_font("DejaVu", size=9)
            pdf.set_text_color(*muted)
            for item in content:
                pdf.multi_cell(0, 5, safe(item), align="L", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
        elif kind == "heading":
            if pdf.get_y() > pdf.h - pdf.b_margin - 35:
                pdf.add_page()  # keep a heading together with the content below it
            pdf.ln(3)
            pdf.set_font("DejaVu", "B", 12)
            pdf.set_text_color(*ink)
            pdf.cell(0, 7, safe(content), new_x="LMARGIN", new_y="NEXT")
            pdf.set_draw_color(*accent)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 24, pdf.get_y())
            pdf.ln(2)
        elif kind == "kv":
            for key, value in content:
                x0, y0 = pdf.get_x(), pdf.get_y()
                pdf.set_font("DejaVu", size=10)
                pdf.set_text_color(*muted)
                pdf.multi_cell(95, 6, safe(key), align="L", new_x="LMARGIN", new_y="NEXT")
                key_bottom = pdf.get_y()
                if key_bottom < y0:  # the label moved to a new page
                    y0 = key_bottom - 6
                pdf.set_xy(x0 + 97, y0)
                pdf.set_font("DejaVu", "B", 10)
                pdf.set_text_color(*ink)
                pdf.cell(0, 6, safe(value), new_x="LMARGIN", new_y="NEXT")
                pdf.set_y(max(key_bottom, y0 + 6))
        elif kind == "note":
            pdf.set_font("DejaVu", size=8.5)
            pdf.set_text_color(*muted)
            pdf.multi_cell(0, 4.5, safe(content), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        elif kind == "bullets":
            pdf.set_font("DejaVu", size=9)
            pdf.set_text_color(*ink)
            for item in content:
                pdf.multi_cell(0, 5, safe("• " + item), new_x="LMARGIN", new_y="NEXT")
        elif kind == "table":
            pdf.set_font("DejaVu", size=8.5)
            pdf.set_text_color(*ink)
            pdf.set_draw_color(*line)
            heading = FontFace(emphasis="BOLD", color=ink, fill_color=(241, 241, 243))
            with pdf.table(col_widths=content["widths"], text_align=content["align"],
                           headings_style=heading, line_height=5.2, width=pdf.epw,
                           borders_layout="HORIZONTAL_LINES", repeat_headings=1) as table:
                head = table.row()
                for col in content["columns"]:
                    head.cell(safe(col))
                for values in content["rows"]:
                    row = table.row()
                    for value in values:
                        row.cell(safe(clip_cell(value)))
            pdf.ln(1)
    return bytes(pdf.output())


# ---------- README.txt ----------

def readme_text(data):
    lines = [
        f"Nomad Life accountant package {data.period.label}",
        "",
    ] + ([f"Prepared for: {data.holder}"] if data.holder else []) + [
        f"Base: {data.base_city}, {country_label(data.base_country, data.base_iso)}",
        f"Period: {data.period.start.isoformat()} to {data.period.end.isoformat()}",
        f"Generated: {data.generated_at.strftime('%Y-%m-%d %H:%M')} (local time) by Nomad Life v{data.app_version}",
        "",
        "Files",
        "  summary.pdf         Readable report: headline numbers, days per country, travel timeline, rules, receipt index.",
        "  timeline.csv        One row per stay" + (" (with notes)." if data.include_notes else " (notes not included)."),
        "  country-totals.csv  One row per country with days and share of the year.",
        "  receipts/           Original receipts, byte for byte. receipts/base holds base documents,",
        "                      the other folders are numbered stays: 01_<start date>_<city>_<country>.",
        "  manifest.csv        Every other file in this package with its size and SHA-256 checksum.",
        "",
        "CSV files are comma separated UTF-8 with a byte order mark. If Excel shows everything in one",
        "column (regions that use a semicolon as list separator), use Data > From Text/CSV and choose comma.",
        "",
        "Rules used for counting",
    ] + [f"  - {rule}" for rule in RULES]
    if data.status == "current":
        lines += ["", f"The year is not over. Numbers are shown so far (as of {data.as_of.isoformat()}) "
                      "and for the full year with the trips already planned."]
    elif data.status == "future":
        lines += ["", "The year has not started yet. Only the trips already planned are counted."]
    if data.missing:
        lines += ["", "Missing receipts (recorded in Nomad Life but not found on the server):"]
        lines += [f"  - {r.name} ({stay_label(data, r.stay_number)})" for r in data.missing]
    lines += ["", DISCLAIMER, ""]
    return "\r\n".join(lines).encode("utf-8")


# ---------- ZIP ----------

class _Sink(io.RawIOBase):
    """Write only, non seekable buffer. zipfile then writes data descriptors instead of
    seeking back, which lets the archive stream out chunk by chunk."""

    def __init__(self):
        super().__init__()
        self._parts = []

    def writable(self):
        return True

    def write(self, data):
        self._parts.append(bytes(data))
        return len(data)

    def take(self):
        data = b"".join(self._parts)
        self._parts.clear()
        return data


def _zip_time(dt):
    return max(dt, datetime(1980, 1, 1)).timetuple()[:6]


def _info(arcname, when, size, fast):
    info = zipfile.ZipInfo(arcname, date_time=_zip_time(when))
    info.compress_type = zipfile.ZIP_DEFLATED
    info._compresslevel = 1 if fast else 6
    info.external_attr = 0o644 << 16
    info.file_size = size
    return info


def prepare(data):
    """Build every generated file up front (errors surface before any byte is sent) and
    return the list of ZIP entries: (arcname, bytes or Receipt, fast compression)."""
    # Group receipts by stay (base first), then by upload date, for the index and manifest.
    data.receipts.sort(key=lambda r: (r.stay_number, r.uploaded, r.name.casefold()))
    assign_arcnames(data)
    root = data.root
    entries = [
        (f"{root}/README.txt", readme_text(data), False),
        (f"{root}/summary.pdf", render_pdf(data), False),
        (f"{root}/timeline.csv", timeline_csv(data), False),
        (f"{root}/country-totals.csv", country_totals_csv(data), False),
    ]
    for r in data.receipts:
        if r.exists:
            entries.append((r.arcname, r, r.ext in FAST_EXTENSIONS))
    for arcname, _, _ in entries:
        _check_arcname(root, arcname)
    return entries


def stream(data, entries):
    """Yield the ZIP in small chunks. Receipts are read 64 KB at a time, so memory stays flat
    whatever the size of the year. manifest.csv comes last, with checksums computed while
    streaming."""
    sink = _Sink()
    manifest = []
    when = data.generated_at
    zf = zipfile.ZipFile(sink, "w")
    try:
        for arcname, source, fast in entries:
            if isinstance(source, bytes):
                with zf.open(_info(arcname, when, len(source), fast), "w") as dest:
                    dest.write(source)
                manifest.append([arcname, "", "generated", when.date().isoformat(), len(source),
                                 hashlib.sha256(source).hexdigest(), "ok"])
                out = sink.take()
                if out:
                    yield out
                continue
            digest, written = hashlib.sha256(), 0
            try:
                fh = open(source.path, "rb")
            except OSError:
                source.exists = False
                continue
            with fh, zf.open(_info(arcname, when, os.fstat(fh.fileno()).st_size, fast), "w") as dest:
                while True:
                    chunk = fh.read(CHUNK)
                    if not chunk:
                        break
                    digest.update(chunk)
                    dest.write(chunk)
                    written += len(chunk)
                    out = sink.take()
                    if out:
                        yield out
            manifest.append([arcname, stay_label(data, source.stay_number), source.kind,
                             source.uploaded, written, digest.hexdigest(), "ok"])
            yield sink.take()
        for r in data.receipts:
            if not r.exists:
                manifest.append([r.arcname, stay_label(data, r.stay_number), r.kind, r.uploaded,
                                 r.size, "", "missing"])
        body = _csv(["path", "stay", "type", "uploaded", "size_bytes", "sha256", "status"], manifest)
        with zf.open(_info(f"{data.root}/manifest.csv", when, len(body), False), "w") as dest:
            dest.write(body)
    finally:
        zf.close()
    yield sink.take()
