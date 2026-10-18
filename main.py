"""
SmartFind — An Intelligent Lost-and-Found Matching System for Campus Items
CS 403 prototype.   Run with:  streamlit run main.py

Perceive -> Reason -> Act
    PERCEIVE : smartfind_vision.py        (YOLO detector + crop-based color estimate + optional OCR)
  REASON   : smartfind_core.py          (working, rule-based similarity score out of 100)
  ACT      : auto_monitor_and_notify()  (in-app notification only; NO real email yet)

Files (keep in the same folder):  main.py, smartfind_core.py, smartfind_vision.py, test_smartfind.py

SECURITY WARNING — Admin Management has NO authentication. Anyone who can open this app
can view, edit, resolve and delete every report. That is acceptable for a classroom demo on
a private network only. Do not deploy publicly without adding login + role checks.

NOTE ON NAVIGATION — a dedicated Notifications page can be added later by appending
("Notifications", "🔔") to PAGES and adding an `elif active == "Notifications":` branch that
lists st.session_state.notifications. Nav keys and highlighting follow PAGES automatically.
"""
import base64
import hashlib
import inspect
import io
import re
from datetime import date, datetime
from html import escape as _esc

import streamlit as st

from smartfind_core import (MATCH_THRESHOLD, NOTIFY_THRESHOLD, calculate_match_score,  # noqa: F401
                            find_pairs, new_notifications)
from smartfind_vision import classify_image, get_model_categories
from smartfind_store import delete_report, load_reports, save_report, seed_reports_if_empty

st.set_page_config(page_title="SmartFind - Campus Lost & Found", page_icon="🔎",
                   layout="wide", initial_sidebar_state="auto")

# ------------------------------------------------------------
# Compatibility check: tests the APIs this app actually uses.
# Recommended: Streamlit >= 1.40 (st.container(key=), st.dialog, selectbox(index=None)).
# ------------------------------------------------------------
def _check_streamlit():
    missing = []
    if "key" not in inspect.signature(st.container).parameters:
        missing.append("st.container(key=...)")
    for name in ("dialog", "rerun", "camera_input"):
        if not hasattr(st, name):
            missing.append(f"st.{name}")
    if "placeholder" not in inspect.signature(st.selectbox).parameters:
        missing.append("st.selectbox(index=None, placeholder=...)")
    if missing:
        st.error(f"This Streamlit version ({st.__version__}) is too old for SmartFind. Missing: "
                 f"{', '.join(missing)}. Run:  pip install -U \"streamlit>=1.40\"")
        st.stop()


_check_streamlit()
# Full-width buttons: newer Streamlit uses width="stretch", older uses **STRETCH.
def _stretch(fn):
    return {"width": "stretch"} if "width" in inspect.signature(fn).parameters else {"use_container_width": True}


STRETCH = _stretch(st.button)
STRETCH_SUBMIT = _stretch(st.form_submit_button)

# ============================================================
# CONFIGURATION  (edit here — nothing else needs to change)
# ============================================================

# Categories come from the trained model; all remain user-confirmable.
MANUAL_ONLY_CATEGORIES = {"Smartphone": "📱", "ID Card": "🪪", "Earphones": "🎧", "Keys": "🔑",
                          "Eyeglasses": "👓", "Water Bottle": "🧴", "Umbrella": "☂️", "Other": "📦"}

MODEL_CATEGORIES = get_model_categories()
AI_CATEGORIES = {name: {"icon": "🔎"} for name in MODEL_CATEGORIES}
ALL_CATEGORIES = list(dict.fromkeys(MODEL_CATEGORIES + list(MANUAL_ONLY_CATEGORIES)))
CATEGORY_ICONS = {**{k: v["icon"] for k, v in AI_CATEGORIES.items()}, **MANUAL_ONLY_CATEGORIES}

PAGES = [
    ("Home", "⌂"),
    ("Report Lost Item", "ⓘ"),
    ("Report Found Item", "⌕"),
    ("Lost & Found Records", "▤"),
    ("Possible Matches", "⛓"),
    ("Admin Management", "♙"),
]

# ============================================================
# HELPERS
# ============================================================

def html(s):
    """Render an HTML block. Indentation/blank lines are stripped so Markdown
    never turns the HTML into a code block."""
    s = "\n".join(l.strip() for l in s.splitlines() if l.strip())
    st.markdown(s, unsafe_allow_html=True)


def esc(text):
    """Escape user text before putting it in HTML ($ is escaped so Streamlit
    doesn't treat it as LaTeX)."""
    return _esc(str(text)).replace("\n", " ").replace("$", "&#36;")


def navigate(page):
    st.session_state.page = page
    st.rerun()


def next_id():
    """IDs are never reused, even after a delete (a reused ID could hide a new notification)."""
    st.session_state.id_counter += 1
    return f"SF-2026-{st.session_state.id_counter:03d}"


def current_pairs():
    # Include legacy records loaded from older databases before score calculation.
    for report in st.session_state.reports:
        report.setdefault("brand", "")
    return find_pairs(st.session_state.reports)


def get_report(rid):
    return next((r for r in st.session_state.reports if r["id"] == rid), None)


def valid_email(e):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e.strip()))


def make_thumbnail(image_bytes):
    """Shrink a photo to a small JPEG data-URI so it can be stored and shown anywhere."""
    try:
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes))).convert("RGB")
        img.thumbnail((420, 420))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def analyze_photo(image_bytes):
    """Cache model/OCR work across Streamlit reruns for the same uploaded photo."""
    return classify_image(image_bytes)


def thumb(r, size=44):
    """Photo thumbnail if the report has one, otherwise a category icon tile."""
    if r.get("photo"):
        return (f'<img src="{r["photo"]}" style="width:{size}px;height:{size}px;object-fit:cover;'
                f'border-radius:8px;flex-shrink:0">')
    icon = CATEGORY_ICONS.get(r["category"], "📦")
    return (f'<div style="width:{size}px;height:{size}px;border-radius:8px;background:#edf2f7;display:flex;'
            f'align-items:center;justify-content:center;font-size:{int(size * .5)}px;flex-shrink:0">{icon}</div>')


def type_pill(r):
    cls = "pill-lost" if r["type"] == "Lost" else "pill-found"
    return f'<span class="pill {cls}">{r["type"]}</span>'


def fmt_date(d):
    try:
        return datetime.strptime(d, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        return d


# ============================================================
# SESSION STATE  (reports are loaded from SQLite on every app rerun)
# ============================================================

st.session_state.setdefault("page", "Home")
st.session_state.setdefault("notifications", [])
st.session_state.setdefault("nonce", 0)          # bumped after a submit to reset the form
st.session_state.setdefault("last_email", "")
st.session_state.setdefault("reports", [
    {"id": "SF-2026-001", "type": "Lost", "item": "Black Scientific Calculator", "category": "Calculator",
     "color": "Black", "location": "Q-5312", "date": "2026-09-30", "photo": None,
     "description": "Casio FX-991EX scientific calculator with black protective cover.",
     "status": "Searching", "user_email": "student1@tip.edu.ph"},
    {"id": "SF-2026-002", "type": "Found", "item": "Black Scientific Calculator", "category": "Calculator",
     "color": "Black", "location": "Q-5312", "date": "2026-10-01", "photo": None,
     "description": "Black Casio scientific calculator left on desk near whiteboard.",
     "status": "Reported", "user_email": "finder1@tip.edu.ph"},
    {"id": "SF-2026-003", "type": "Found", "item": "Blue Water Bottle", "category": "Water Bottle",
     "color": "Blue", "location": "Library — 2nd Floor", "date": "2026-10-02", "photo": None,
     "description": "Aquaflask stainless water bottle with stickers.",
     "status": "Reported", "user_email": "finder2@tip.edu.ph"},
    {"id": "SF-2026-004", "type": "Lost", "item": "Student ID Lanyard", "category": "ID Card",
     "color": "Navy Blue", "location": "Study Hall", "date": "2026-10-03", "photo": None,
     "description": "TIP student ID card with blue lanyard and key holder.",
     "status": "Searching", "user_email": "student2@tip.edu.ph"},
])

st.session_state.setdefault(
    "id_counter",
    max((int(r["id"].split("-")[-1]) for r in st.session_state.reports), default=0))

if not st.session_state.get("reports_loaded", False):
    seed_reports_if_empty(st.session_state.reports)
    st.session_state.reports_loaded = True
st.session_state.reports = load_reports()
for _report in st.session_state.reports:
    _report.setdefault("brand", "")
st.session_state.id_counter = max(
    st.session_state.id_counter,
    max((int(r["id"].split("-")[-1]) for r in st.session_state.reports), default=0))

# ============================================================
# AGENT 1 — PERCEIVE: see smartfind_vision.py (YOLO detection, crop analysis, optional OCR)
# ============================================================

# ============================================================
# AGENT 2 — REASON: see smartfind_core.py (rule-based similarity score out of 100)
# ============================================================

# ============================================================
# AGENT 3 — ACT   (in-app notification only — SIMULATED, no email is sent)
# ============================================================

def auto_monitor_and_notify():
    """Create at most one simulated notification per lost/found pair."""
    keys = {n["key"] for n in st.session_state.notifications}
    st.session_state.notifications += new_notifications(
        st.session_state.reports, keys, datetime.now().strftime("%b %d, %I:%M %p"))


auto_monitor_and_notify()

# ============================================================
# CSS
# ============================================================

active = st.session_state.page
active_idx = [p for p, _ in PAGES].index(active)

# ------------------------------------------------------------
# TEXT CONTRAST
# Cause of the "white text on white card" bug: the app paints its own light surfaces
# (white cards, light page background) but never set a text color. Streamlit's default
# text color follows the user's OS/browser theme, which is WHITE in dark mode. So every
# label/heading/caption/radio option that Streamlit draws itself became white-on-white.
# Fix: force dark text ONLY on elements that sit directly on the surfaces we paint white.
# Inputs, dropdown menus and other themed widgets keep their own (self-consistent) colors,
# and our own HTML keeps its inline colors (descendants with their own color are unaffected).
# ------------------------------------------------------------
NAVY, SLATE = "#0d2847", "#4a5568"
LIGHT_SURFACES = ['div[class*="st-key-card_"]',                   # all white cards
                  '[data-testid="stDialog"] div[role="dialog"]',  # dialogs (forced white below)
                  '[data-testid="stExpander"]']                   # expanders (forced white below)


def _on_light(suffixes, rules):
    sel = ", ".join(f"{surface} {suffix}".strip() for surface in LIGHT_SURFACES for suffix in suffixes)
    return f"{sel} {{ {rules} }}\n"


_MD = '[data-testid="stMarkdownContainer"]'
_BTN = ['[data-testid^="stBaseButton-secondary"]', '.stButton > button']
light_css = "".join([
    # headings, paragraphs, list items rendered by st.markdown / st.write / st.info etc.
    _on_light([_MD] + [f"{_MD} {t}" for t in ("p", "li", "h1", "h2", "h3", "h4", "h5", "h6")],
              f"color:{NAVY} !important;"),
    # form labels (text_input, selectbox, date_input, text_area, radio titles...)
    _on_light(['[data-testid="stWidgetLabel"]', '[data-testid="stWidgetLabel"] p',
               '[data-testid="stWidgetLabel"] label'], f"color:{NAVY} !important; font-weight:600;"),
    # radio option labels
    _on_light(['[data-testid="stRadio"] label', '[data-testid="stRadio"] label p'],
              f"color:{NAVY} !important;"),
    # st.caption descriptions
    _on_light(['[data-testid="stCaptionContainer"]', '[data-testid="stCaptionContainer"] p'],
              f"color:{SLATE} !important;"),
    # normal buttons: white with navy text (primary buttons keep the theme's primary color)
    _on_light(_BTN, f"background:#fff !important; color:{NAVY} !important; border:1px solid #cbd5e0 !important;"),
    _on_light([f"{b} p" for b in _BTN], f"color:{NAVY} !important;"),
    _on_light([f"{b}:hover" for b in _BTN], "border-color:#2aa99b !important;"),
    _on_light([f"{b}:disabled" for b in _BTN], "color:#a0aec0 !important; background:#f7fafc !important;"),
])

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"] {{ font-family: 'Inter', sans-serif; }}
.stApp {{ background:#f4f7fb; }}

/* Hide Streamlit chrome but keep the sidebar toggle */
#MainMenu, footer, [data-testid="stDecoration"], [data-testid="stToolbar"] {{ display:none !important; }}
header[data-testid="stHeader"] {{ background:transparent; height:0; }}
.block-container {{ padding:0 0 3rem 0 !important; max-width:100% !important; }}
.st-key-body_pad {{ padding:0 35px; }}

/* Sidebar */
section[data-testid="stSidebar"], section[data-testid="stSidebar"] > div {{ background:#0d2847 !important; }}
section[data-testid="stSidebar"] {{ min-width:260px !important; max-width:260px !important; }}
section[data-testid="stSidebar"] .stButton > button {{
  width:100%; justify-content:flex-start; border:1px solid transparent; background:transparent;
  color:#d1e2f2; font-size:13px; font-weight:600; border-radius:10px; padding:10px 14px; box-shadow:none; }}
section[data-testid="stSidebar"] .stButton > button > div {{ width:100%; justify-content:flex-start !important; }}
section[data-testid="stSidebar"] .stButton > button p {{ text-align:left !important; width:100%; }}
section[data-testid="stSidebar"] .stButton > button:hover {{ background:rgba(255,255,255,.08); color:#fff; }}
.st-key-nav_{active_idx} button {{
  background:rgba(42,169,155,.18) !important; color:#fff !important; border:1px solid #2aa99b !important; }}
.logo-row {{ display:flex; align-items:center; gap:12px; padding:24px 8px 18px; border-bottom:1px solid rgba(255,255,255,.08); }}
.logo-icon {{ width:44px; height:44px; border-radius:12px; background:#2aa99b; color:#fff; display:flex;
  align-items:center; justify-content:center; font-weight:800; font-size:18px; }}
.menu-label {{ color:#6482a0; font-size:10px; font-weight:800; letter-spacing:1px; padding:20px 8px 8px; }}

/* Top header */
.top-header {{ height:60px; background:#fff; border-bottom:1px solid #e2e8f0; display:flex;
  align-items:center; justify-content:space-between; padding:0 35px; margin-bottom:24px; }}
.breadcrumb {{ color:#718096; font-size:13px; }} .breadcrumb strong {{ color:#1a202c; }}
.user-avatar {{ width:32px; height:32px; border-radius:50%; background:#e2e8f0; color:#2b6cb0;
  display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:700; }}

/* Cards: any container whose key starts with card_ */
div[class*="st-key-card_"] {{ background:#fff; border:1px solid #e2e8f0; border-radius:14px; padding:22px; }}
div[class*="st-key-card_"] .stButton > button {{ border-radius:8px; font-weight:600; font-size:12px; }}

/* Hero */
.st-key-hero {{ background:linear-gradient(110deg,#0e2a47 0%,#15395f 55%,#1c525f 100%); border-radius:18px;
  padding:36px 40px; box-shadow:0 10px 25px rgba(13,40,71,.12); }}
.st-key-hero .stButton > button {{ border-radius:8px; font-weight:700; font-size:13px; }}
.st-key-hero_lost button {{ background:#2aa99b; color:#fff; border:none; }}
.st-key-hero_found button {{ background:transparent; color:#fff; border:1px solid rgba(255,255,255,.4); }}
.hero-badge {{ background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.2); border-radius:20px;
  padding:4px 12px; font-size:10px; font-weight:700; color:#e2e8f0; display:inline-block; margin-bottom:14px; }}
.hero-title {{ font-size:40px; font-weight:800; line-height:1.15; color:#fff; margin-bottom:12px; }}
.hero-title span {{ color:#2aa99b; }}
.hero-desc {{ color:#cddbe8; font-size:13px; line-height:1.6; margin-bottom:18px; max-width:520px; }}
.how-card {{ background:#fff; border-radius:14px; padding:20px; color:#1a202c; box-shadow:0 8px 20px rgba(0,0,0,.15); }}
.step {{ display:flex; gap:12px; margin-top:14px; }}
.step-num {{ width:24px; height:24px; border-radius:50%; background:#ebf8ff; color:#2b6cb0; font-weight:800;
  font-size:11px; display:flex; align-items:center; justify-content:center; flex-shrink:0; }}

.eyebrow {{ color:#2a9d8f; font-size:11px; font-weight:800; letter-spacing:1px; }}
.page-title {{ font-size:30px; font-weight:500; color:#0d2847; margin:2px 0 4px; }}
.page-sub {{ color:#718096; font-size:13px; margin-bottom:18px; }}

.pill {{ padding:3px 12px; border-radius:12px; font-size:11px; font-weight:700; display:inline-block; }}
.pill-lost {{ background:#fffaf0; color:#dd6b20; border:1px solid #fbd38d; }}
.pill-found {{ background:#f0fff4; color:#2f855a; border:1px solid #9ae6b4; }}
.pill-open {{ background:#edf2f7; color:#4a5568; }}
.th {{ font-size:10px; font-weight:800; color:#718096; letter-spacing:.5px; }}
.notif-bar {{ background:#ebf8ff; border:1px solid #bee3f8; border-radius:12px; padding:12px 16px;
  margin-bottom:18px; color:#2a4365; font-size:12px; line-height:1.5; }}
{light_css}
/* Dialogs and expanders are themed dark in dark mode; make them white to match the cards */
[data-testid="stDialog"] div[role="dialog"] {{ background:#fff !important; }}
[data-testid="stDialog"] div[role="dialog"] h1, [data-testid="stDialog"] div[role="dialog"] h2,
[data-testid="stDialog"] div[role="dialog"] h3 {{ color:{NAVY} !important; }}
[data-testid="stDialog"] button[aria-label="Close"] {{ color:{SLATE} !important; }}
[data-testid="stExpander"] {{ background:#fff; border:1px solid #e2e8f0 !important; border-radius:12px; }}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p,
[data-testid="stExpander"] summary span {{ color:{NAVY} !important; }}
[data-testid="stExpander"] summary svg {{ fill:{NAVY} !important; color:{NAVY} !important; }}
/* Alerts (st.success / info / warning / error) sit on the light page: dark text on their tinted fill */
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
[data-testid="stAlert"] [data-testid="stMarkdownContainer"] li {{ color:#1a202c !important; }}

.warn-bar {{ background:#fffaf0; border:1px solid #fbd38d; border-radius:12px; padding:12px 16px;
  margin-bottom:18px; color:#7b341e; font-size:12px; line-height:1.5; }}
.sim-tag {{ background:#fefcbf; color:#744210; font-size:9px; font-weight:800; padding:2px 7px;
  border-radius:6px; letter-spacing:.5px; margin-right:6px; }}
.lbl {{ display:none; color:#718096; font-size:12px; }}
.rid {{ font-size:10px; color:#a0aec0; }}
.stat-icon {{ width:34px; height:34px; border-radius:8px; background:#e6fffa; display:flex;
  align-items:center; justify-content:center; font-size:16px; flex-shrink:0; }}

/* Records list: table-like on desktop, stacked cards on phones */
.rec-head, .rec-summary {{ display:grid; grid-template-columns:3fr 1.3fr 1fr 1.8fr 1.3fr 1fr 1fr;
  gap:10px; align-items:center; }}
.rec-head {{ padding:8px 6px; border-bottom:1px solid #e2e8f0; }}
details.rec {{ border-bottom:1px solid #edf2f7; }}
details.rec > summary {{ list-style:none; cursor:pointer; padding:12px 6px; font-size:13px; }}
details.rec > summary::-webkit-details-marker {{ display:none; }}
.cell-name {{ display:flex; gap:10px; align-items:center; min-width:0; }}
.rec-more {{ color:#2b6cb0; font-weight:700; font-size:12px; text-align:right; }}
details.rec[open] .rec-more {{ color:#2a9d8f; }}
.rec-detail {{ background:#f8fafc; border-radius:10px; padding:14px; margin:0 6px 12px; display:flex;
  gap:14px; font-size:12px; color:#4a5568; line-height:1.8; }}

/* Match cards */
.m-top {{ display:flex; gap:16px; align-items:stretch; }}
.m-link {{ color:#718096; font-size:20px; align-self:center; }}
.m-bottom {{ display:flex; margin-top:16px; border:1px solid #e2e8f0; border-radius:12px; overflow:hidden; }}

/* ---------- PHONE ---------- */
@media (max-width: 700px) {{
  .top-header {{ padding:0 14px 0 56px; margin-bottom:16px; }}
  .top-header .user-name {{ display:none; }}
  .st-key-body_pad {{ padding:0 14px !important; }}
  .st-key-hero {{ padding:22px 18px; border-radius:14px; }}
  .hero-title {{ font-size:30px; }}
  .page-title {{ font-size:24px; }}
  div[class*="st-key-card_"] {{ padding:14px; }}
  .m-top {{ flex-direction:column; align-items:stretch; gap:8px; }}
  .m-link {{ text-align:center; transform:rotate(90deg); align-self:center; }}
  .m-bottom {{ flex-direction:column; }}
  .score-panel {{ width:100% !important; box-sizing:border-box; }}
  .lbl {{ display:inline; }}
  .rec-head {{ display:none; }}
  details.rec {{ border:1px solid #e2e8f0; border-radius:12px; margin-bottom:10px; background:#fff; }}
  .rec-summary {{ grid-template-columns:1fr 1fr; gap:6px 10px; }}
  .rec-summary > .cell-name {{ grid-column:1 / -1; }}
  .rec-more {{ text-align:left; }}
  .rec-detail {{ flex-direction:column; margin:0 8px 10px; }}
  .stButton > button {{ min-height:44px; }}
  /* Admin rows become cards */
  div[class*="st-key-th_"] {{ display:none; }}
  div[class*="st-key-row_"] {{ border:1px solid #e2e8f0; border-radius:10px; padding:10px; margin-bottom:10px; background:#fbfcfe; }}
  div[class*="st-key-row_"] [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap; gap:6px; }}
  div[class*="st-key-row_"] [data-testid="stColumn"], div[class*="st-key-row_"] [data-testid="column"] {{
    min-width:calc(50% - 6px) !important; flex:1 1 calc(50% - 6px) !important; width:auto !important; }}
  div[class*="st-key-row_"] [data-testid="stColumn"]:first-child, div[class*="st-key-row_"] [data-testid="column"]:first-child {{
    min-width:100% !important; }}
}}
</style>
""", unsafe_allow_html=True)

# ============================================================
# SHARED DIALOG
# ============================================================

@st.dialog("Report details")
def show_details(rid):
    r = get_report(rid)
    if not r:
        st.write("This report no longer exists.")
        return
    html(f"""{type_pill(r)} <span class="rid" style="font-size:12px;margin-left:8px">{r['id']}</span>
    <div style="display:flex;gap:14px;margin-top:10px">{thumb(r, 88)}
    <div><div style="font-size:18px;font-weight:800">{esc(r['item'])}</div>
    <div style="font-size:13px;color:#4a5568;line-height:1.9">
    Category: <strong>{esc(r['category'])}</strong><br>Brand: <strong>{esc(r.get('brand', '')) or '—'}</strong><br>
    Color: <strong>{esc(r['color']) or '—'}</strong><br>
    Location: <strong>{esc(r['location'])}</strong><br>Date: <strong>{fmt_date(r['date'])}</strong><br>
    Status: <strong>{r['status']}</strong><br>Description: {esc(r['description']) or '—'}</div></div></div>""")


# ============================================================
# SIDEBAR + HEADER
# ============================================================

with st.sidebar:
    html("""
    <div class="logo-row"><div class="logo-icon">SF</div>
    <div><div style="color:#fff;font-size:18px;font-weight:800;line-height:1.1">SmartFind</div>
    <div style="color:#8da4be;font-size:11px">Campus Lost & Found</div></div></div>
    <div class="menu-label">MAIN MENU</div>
    """)
    for i, (name, icon) in enumerate(PAGES):
        if st.button(f"{icon}   {name}", key=f"nav_{i}", **STRETCH):
            navigate(name)
    html("""
    <div style="margin-top:60px;background:rgba(255,255,255,.05);padding:10px 12px;border-radius:8px;
    border:1px solid rgba(255,255,255,.1)">
    <div style="color:#2aa99b;font-size:10px;font-weight:800">TIP CS 403 PROJECT</div>
    <div style="color:#a0aec0;font-size:10px;margin-top:2px">Perceive → Reason → Act</div></div>
    """)

html(f"""
<div class="top-header">
  <div class="breadcrumb">Campus Lost & Found &nbsp;›&nbsp; <strong>{active}</strong></div>
  <div style="display:flex;align-items:center;gap:10px">
    <div class="user-avatar">U</div>
    <div class="user-name"><div style="color:#2d3748;font-size:12px;font-weight:700">Campus User</div>
    <div style="color:#a0aec0;font-size:10px">Student access</div></div>
  </div>
</div>
""")

body = st.container(key="body_pad")

with body:
    # Simulated in-app notification banner (Home + Possible Matches only)
    if st.session_state.notifications and active in ("Home", "Possible Matches"):
        n = st.session_state.notifications[-1]
        html(f"""<div class="notif-bar"><span class="sim-tag">SIMULATED</span>
        <strong>In-app notification ({n['time']}):</strong> SmartFind found a possible match for
        <strong>"{esc(n['item'])}"</strong> (similarity score {n['score']}/100). A real email to {esc(n['recipient'])}
        would be sent here once email delivery is built. <strong>No email was sent.</strong></div>""")

    # ========================================================
    # HOME
    # ========================================================
    if active == "Home":
        with st.container(key="hero"):
            left, right = st.columns([1.3, 1], gap="large")
            with left:
                html("""
                <div class="hero-badge">CS 403 • INTELLIGENT AGENTS PROJECT</div>
                <div class="hero-title">Find it. Report it.<br><span>Get it back.</span></div>
                <div class="hero-desc">Report a lost or found item in a few taps. SmartFind compares new
                reports against existing ones and flags possible matches.</div>
                """)
                b1, b2, _ = st.columns([1, 1, 1])
                with b1, st.container(key="hero_lost"):
                    if st.button("Report a lost item", **STRETCH):
                        navigate("Report Lost Item")
                with b2, st.container(key="hero_found"):
                    if st.button("Report a found item", **STRETCH):
                        navigate("Report Found Item")
            with right:
                html("""
                <div class="how-card">
                  <div style="display:flex;justify-content:space-between;align-items:center;
                  border-bottom:1px solid #edf2f7;padding-bottom:10px">
                    <div style="font-size:13px;font-weight:800">How SmartFind works</div>
                    <span style="background:#e6fffa;color:#234e52;font-size:9px;font-weight:800;
                    padding:2px 8px;border-radius:6px">INTELLIGENT</span></div>
                  <div class="step"><div class="step-num">1</div><div>
                    <div style="font-size:12px;font-weight:700">Perceive</div>
                    <div style="font-size:11px;color:#718096">YOLO detects a supported item, crops it, and suggests category, pixel color, and readable text for review.</div></div></div>
                  <div class="step"><div class="step-num">2</div><div>
                    <div style="font-size:12px;font-weight:700">Reason</div>
                    <div style="font-size:11px;color:#718096">Compares category, color, brand text, location, date and description.</div></div></div>
                  <div class="step"><div class="step-num">3</div><div>
                    <div style="font-size:12px;font-weight:700">Act</div>
                    <div style="font-size:11px;color:#718096">Notifies you about strong possible matches.</div></div></div>
                </div>
                """)

        st.write("")
        st.markdown('<div class="eyebrow">MAIN MENU</div>', unsafe_allow_html=True)
        st.markdown('<div class="page-title" style="font-size:22px">What would you like to do?</div>',
                    unsafe_allow_html=True)
        cards = [
            ("ⓘ", "Report Lost Item", "Add details about an item you misplaced.", "Report Lost Item"),
            ("⌕", "Report Found Item", "Record an item found around campus.", "Report Found Item"),
            ("⛓", "View Possible Matches", "Review matches calculated from report details.", "Possible Matches"),
            ("▤", "View Lost & Found Records", "Search all submitted campus item records.", "Lost & Found Records"),
        ]
        for n_card, (col, (ic, title, sub, target)) in enumerate(zip(st.columns(4), cards)):
            with col, st.container(key=f"card_home_{n_card}"):
                html(f"""<div style="display:flex;gap:12px;min-height:84px">
                <div class="stat-icon">{ic}</div><div>
                <div style="font-size:13px;font-weight:700;color:#1a202c">{title}</div>
                <div style="font-size:11px;color:#718096;margin-top:2px">{sub}</div></div></div>""")
                if st.button("Open", key=f"open_{n_card}", **STRETCH):
                    navigate(target)

        st.write("")
        chips = "".join(
            f'<div style="background:#f7fafc;border:1px solid #e2e8f0;padding:10px 16px;border-radius:10px;'
            f'font-size:12px;font-weight:700">{v["icon"]} {k}</div>' for k, v in AI_CATEGORIES.items())
        with st.container(key="card_supported"):
            html(f"""<div class="eyebrow">PHOTO RECOGNITION · YOLOv8</div>
            <div style="font-size:16px;font-weight:800;color:#1a202c;margin-top:4px">{len(MODEL_CATEGORIES)} trained model categories</div>
            <div style="color:#718096;font-size:12px;margin:4px 0 14px">YOLO suggests an item and bounding box. OCR reads visible text (not logos), while color is estimated from crop pixels. Confirm suggestions before saving.</div>
            <div style="display:flex;gap:12px;flex-wrap:wrap">{chips}</div>""")

        st.write("")
        status_rows = [
            ("Photo recognition", f"{'YOLOv8 model loaded' if MODEL_CATEGORIES else 'Model unavailable — manual reports still work'}; category, crop, OCR text and color suggestions", "ok" if MODEL_CATEGORIES else "warn"),
            ("Matching agent", "Working — rule-based scoring", "ok"),
            ("Notifications", "In-app only (simulated) — no email is sent yet", "warn"),
            ("Storage", "SQLite — reports persist across browser sessions on the configured disk", "ok"),
            ("Admin access", "No login — classroom prototype only, not secure for real use", "warn"),
        ]
        items = "".join(
            f'<div style="display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #edf2f7;font-size:12px">'
            f'<span style="color:{"#2f855a" if s == "ok" else "#dd6b20"};font-weight:800">{"●" if s == "ok" else "◐"}</span>'
            f'<div><strong>{a}</strong><div style="color:#718096">{b}</div></div></div>' for a, b, s in status_rows)
        with st.container(key="card_status"):
            html(f"""<div class="eyebrow">PROTOTYPE STATUS</div>
            <div style="font-size:16px;font-weight:800;color:#1a202c;margin:4px 0 8px">What works today</div>{items}""")

    # ========================================================
    # REPORT LOST / FOUND  (same workflow, different wording)
    # ========================================================
    elif active in ("Report Lost Item", "Report Found Item"):
        is_lost = active == "Report Lost Item"
        rtype = "Lost" if is_lost else "Found"
        nonce = st.session_state.nonce
        verb = "lost" if is_lost else "found"

        html(f"""<div class="eyebrow">NEW {rtype.upper()} REPORT</div>
        <div class="page-title">Report {rtype} Item</div>
        <div class="page-sub">A photo is optional. YOLO, OCR text, and pixel color are suggestions only; confirm or correct them. Fields marked * are required.</div>""")

        flash = st.session_state.pop("flash", None)
        if flash:
            getattr(st, flash[0])(flash[1])

        with st.container(key="card_form"):
            # ---------------- Step 1: photo ----------------
            st.markdown("##### 📷 Step 1 — Add a photo")
            st.caption("Optional. The trained YOLO model detects an item crop; OCR and color are best-effort suggestions that you can edit.")
            UP, CAM, NONE = "📁 Choose / upload", "📸 Take photo", "✍️ No photo"
            mode = st.radio("Photo source", [UP, CAM, NONE], horizontal=True,
                            label_visibility="collapsed", key=f"mode_{rtype}_{nonce}")
            photo_file = None
            if mode == UP:
                photo_file = st.file_uploader("Photo", type=["jpg", "jpeg", "png"],
                                              label_visibility="collapsed", key=f"up_{rtype}_{nonce}")
            elif mode == CAM:
                photo_file = st.camera_input("Take a photo", label_visibility="collapsed",
                                             key=f"cam_{rtype}_{nonce}")
            else:
                st.info("No problem — you can report without a photo. Just fill in the details below.")

            photo_bytes = photo_file.getvalue() if photo_file else None
            photo_uri, suggestion = None, None
            if photo_bytes:
                photo_uri = make_thumbnail(photo_bytes)
                try:
                    with st.spinner("Detecting item and analyzing its crop…"):
                        suggestion = analyze_photo(photo_bytes)
                except (NotImplementedError, FileNotFoundError, RuntimeError, ValueError) as exc:
                    suggestion = {"category": None, "confidence": None, "color": None,
                                  "brand": None, "brand_candidates": [], "text_candidates": [],
                                  "message": str(exc)}
                    st.warning(f"Photo analysis is unavailable: {esc(exc)}. You can still enter the report manually.")
                img_tag = (f'<img src="{photo_uri}" style="width:84px;height:84px;object-fit:cover;'
                           f'border-radius:10px;flex-shrink:0">') if photo_uri else ""
                if suggestion and suggestion["category"]:
                    cat_icon = CATEGORY_ICONS.get(suggestion["category"], "")
                    tag = '<span class="sim-tag">YOLO SUGGESTION</span>'
                    conf = suggestion["confidence"]
                    note = (f"Detection confidence: <strong>{conf:.0f}%</strong>; not a match probability. "
                            "Confirm or change the category. Brand text and color are separate suggestions.")
                    brand_suggestions = suggestion.get("brand_candidates", [])
                    if brand_suggestions:
                        candidate_text = ", ".join(esc(candidate) for candidate in brand_suggestions)
                        note += f' Possible brand-name reads: <strong>{candidate_text}</strong> (confirm manually).'
                    other_text = suggestion.get("text_candidates", [])
                    if other_text:
                        candidate_text = ", ".join(esc(candidate) for candidate in other_text)
                        note += f' Other OCR text: {candidate_text}.'
                    if suggestion.get("color"):
                        note += f' Pixel color estimate: <strong>{esc(suggestion["color"])}</strong> (confirm or edit).'
                    body_txt = (f'<div style="color:#2b6cb0;font-size:10px;font-weight:800">{tag}SUGGESTED CATEGORY</div>'
                                f'<div style="font-size:17px;font-weight:800;color:#1a202c">{cat_icon} {suggestion["category"]}</div>'
                                f'<div style="color:#4a5568;font-size:11px;margin-top:4px;line-height:1.5">{note}</div>')
                else:
                    fallback_notes = []
                    if suggestion.get("color"):
                        fallback_notes.append(
                            f'Central-image color estimate: <strong>{esc(suggestion["color"])}</strong> '
                            '(background may affect it).')
                    if suggestion.get("brand_candidates"):
                        likely = ", ".join(esc(value) for value in suggestion["brand_candidates"])
                        fallback_notes.append(f'Possible known brand text: <strong>{likely}</strong>.')
                    if suggestion.get("text_candidates"):
                        raw_text = ", ".join(esc(value) for value in suggestion["text_candidates"])
                        fallback_notes.append(f'Readable photo text: {raw_text}.')
                    fallback_detail = " ".join(fallback_notes)
                    body_txt = ('<div style="color:#718096;font-size:10px;font-weight:800">NO SUGGESTION</div>'
                                '<div style="font-size:13px;font-weight:700;color:#1a202c">No item category detected; image analysis still ran.</div>'
                                f'<div style="color:#4a5568;font-size:11px;margin-top:4px">'
                                f'{esc(suggestion.get("message") or "No trained object was detected.")} '
                                f'{fallback_detail} Choose the category manually below.</div>')
                html(f"""<div style="display:flex;gap:14px;align-items:center;background:#ebf8ff;border:1px solid #bee3f8;
                padding:14px;border-radius:12px;margin-top:10px">{img_tag}<div>{body_txt}</div></div>""")

            st.divider()

            # ---------------- Step 2: details ----------------
            st.markdown("##### 📝 Step 2 — Confirm the details")
            sug_cat = suggestion["category"] if suggestion else None
            sug_col = (suggestion.get("color") if suggestion else None) or ""
            brand_candidates = suggestion.get("brand_candidates", []) if suggestion else []
            text_candidates = suggestion.get("text_candidates", []) if suggestion else []
            sig = hashlib.md5(photo_bytes).hexdigest()[:8] if photo_bytes else "nophoto"
            sug_brand = ""
            ocr_options = list(dict.fromkeys(brand_candidates + text_candidates))
            if ocr_options:
                choice = st.selectbox(
                    "OCR text read from photo (optional; confirm if this is the brand)",
                    ["Manual entry"] + ocr_options,
                    key=f"ocr_brand_{rtype}_{nonce}_{sig}")
                if choice != "Manual entry":
                    sug_brand = choice
            default_name = f"{sug_col} {sug_cat}".strip() if sug_cat else ""

            with st.form(f"form_{rtype}_{nonce}_{sig}"):
                cat = st.selectbox("Item category *", ALL_CATEGORIES,
                                   index=ALL_CATEGORIES.index(sug_cat) if sug_cat in ALL_CATEGORIES else None,
                                   placeholder="Select a category")
                name = st.text_input("Item name (optional)", value=default_name,
                                     placeholder="Filled in from the category if left blank")
                color = st.text_input("Color (optional; confirm image estimate)", value=sug_col, placeholder="e.g. Black")
                brand = st.text_input("Brand (optional; confirm/correct OCR text or enter manually)", value=sug_brand,
                                      placeholder="e.g. ASUS — OCR reads text, not logos")
                loc = st.text_input(f"Where was it {'last seen' if is_lost else 'found'}? *",
                                    placeholder="e.g. Q-5312, Library 2nd Floor")
                dt = st.date_input(f"Date {verb} *", value=date.today(), max_value=date.today())
                desc = st.text_area("Description (optional)",
                                    placeholder="Brand, case, stickers, scratches or other distinguishing features")
                email = st.text_input("Contact email * (for notifications)", value=st.session_state.last_email,
                                      placeholder="you@tip.edu.ph")
                submit = st.form_submit_button(f"Submit {verb} report", **STRETCH_SUBMIT)

            if submit:
                errors = []
                if not cat:
                    errors.append("Please choose an item category.")
                if not loc.strip():
                    errors.append(f"Please enter where the item was {'last seen' if is_lost else 'found'}.")
                if not valid_email(email):
                    errors.append("Please enter a valid contact email.")
                for e in errors:
                    st.error(e)
                if not errors:
                    color_v = color.strip()
                    item_name = name.strip()
                    if not item_name or (item_name == default_name and cat != sug_cat):
                        item_name = f"{color_v} {cat}".strip()
                    rid = next_id()
                    st.session_state.reports.append({
                        "id": rid, "type": rtype, "item": item_name, "category": cat, "color": color_v,
                        "brand": brand.strip(), "location": loc.strip(), "date": dt.strftime("%Y-%m-%d"), "description": desc.strip(),
                        "photo": photo_uri, "status": "Searching" if is_lost else "Reported",
                        "user_email": email.strip()})
                    save_report(st.session_state.reports[-1])
                    st.session_state.last_email = email.strip()
                    before = len(st.session_state.notifications)
                    auto_monitor_and_notify()
                    n_matches = sum(1 for p in current_pairs() if rid in (p[1]["id"], p[2]["id"]))
                    msg = f"Report {rid} saved. "
                    if n_matches:
                        msg += f"SmartFind found {n_matches} possible match(es) — see Possible Matches."
                        if len(st.session_state.notifications) > before:
                            msg += " A simulated in-app notification was created (no email sent)."
                        st.session_state.flash = ("success", msg)
                    else:
                        msg += "No possible matches yet — SmartFind will compare it with new reports as they arrive."
                        st.session_state.flash = ("success", msg)
                    st.session_state.nonce += 1
                    st.rerun()

    # ========================================================
    # RECORDS
    # ========================================================
    elif active == "Lost & Found Records":
        html("""<div class="eyebrow">CAMPUS RECORDS</div><div class="page-title">Lost & Found Records</div>
        <div class="page-sub">Search and review submitted lost and found reports. Tap a report to see its details.</div>""")

        q = st.text_input("Search", placeholder="Search by item name, category, brand or location…",
                          label_visibility="collapsed")
        f1, f2, f3 = st.columns(3)
        type_f = f1.selectbox("Type", ["Lost & Found", "Lost", "Found"], label_visibility="collapsed")
        cat_f = f2.selectbox("Category", ["All categories"] + ALL_CATEGORIES, label_visibility="collapsed")
        stat_f = f3.selectbox("Status", ["All statuses"] + sorted({r["status"] for r in st.session_state.reports}),
                              label_visibility="collapsed")

        recs = list(reversed(st.session_state.reports))  # newest first
        if q:
            ql = q.lower()
            recs = [r for r in recs if ql in r["item"].lower() or ql in r["category"].lower()
                    or ql in r.get("brand", "").lower() or ql in r["location"].lower()]
        if type_f != "Lost & Found":
            recs = [r for r in recs if r["type"] == type_f]
        if cat_f != "All categories":
            recs = [r for r in recs if r["category"] == cat_f]
        if stat_f != "All statuses":
            recs = [r for r in recs if r["status"] == stat_f]

        rows_html = ""
        for r in recs:
            rows_html += f"""<details class="rec"><summary class="rec-summary">
            <div class="cell-name">{thumb(r, 44)}<div style="min-width:0"><strong>{esc(r['item'])}</strong>
            <div class="rid">{r['id']}</div></div></div>
            <div><span class="lbl">Category: </span>{esc(r['category'])}</div>
            <div><span class="lbl">Color: </span>{esc(r['color']) or '—'}</div>
            <div><span class="lbl">Location: </span>{esc(r['location'])}</div>
            <div><span class="lbl">Date: </span>{fmt_date(r['date'])}</div>
            <div>{type_pill(r)}<div class="rid">{r['status']}</div></div>
            <div class="rec-more">View details ▾</div></summary>
            <div class="rec-detail">{thumb(r, 96)}<div>
            <strong style="font-size:14px;color:#1a202c">{esc(r['item'])}</strong><br>
            Category: <strong>{esc(r['category'])}</strong> · Brand: <strong>{esc(r.get('brand', '')) or '—'}</strong> ·
            Color: <strong>{esc(r['color']) or '—'}</strong><br>
            {'Last seen' if r['type'] == 'Lost' else 'Found'} at <strong>{esc(r['location'])}</strong> on <strong>{fmt_date(r['date'])}</strong><br>
            Status: <strong>{r['status']}</strong><br>
            Description: {esc(r['description']) or '—'}</div></div></details>"""

        with st.container(key="card_records"):
            html(f"""<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">
            <strong style="font-size:13px">Recent records</strong>
            <span class="pill pill-open">{len(recs)} reports</span></div>
            <div class="rec-head"><span class="th">ITEM NAME</span><span class="th">CATEGORY</span>
            <span class="th">COLOR</span><span class="th">LOCATION</span><span class="th">DATE</span>
            <span class="th">TYPE / STATUS</span><span></span></div>""")
            if recs:
                html(rows_html)
            else:
                st.info("No reports match your search. Try clearing the filters.")

    # ========================================================
    # POSSIBLE MATCHES
    # ========================================================
    elif active == "Possible Matches":
        html("""<div class="eyebrow">SMARTFIND MATCHING AGENT</div><div class="page-title">Possible Matches</div>
        <div class="page-sub">SmartFind compares lost and found reports automatically. The similarity score (out of 100) shows how
        alike the report details are. It is <strong>not a probability and not proof of ownership</strong>,
        and it is separate from any photo-recognition confidence.</div>""")

        pairs = current_pairs()
        notified = {n["key"] for n in st.session_state.notifications}

        def side(label, color, r):
            return f"""<div style="flex:1;min-width:0;background:#fff;border:1px solid #e2e8f0;border-top:3px solid {color};
            border-radius:10px;padding:16px">
            <div style="display:flex;justify-content:space-between"><span style="color:{color};font-size:10px;
            font-weight:800">{label}</span><span class="rid">{r['id']}</span></div>
            <div style="display:flex;gap:12px;margin:8px 0 10px;align-items:center">{thumb(r, 56)}
            <div style="font-size:15px;font-weight:700">{esc(r['item'])}</div></div>
            <div style="font-size:12px;color:#4a5568;line-height:1.8">Category: <strong>{esc(r['category'])}</strong><br>
            Brand: <strong>{esc(r.get('brand', '')) or '—'}</strong><br>
            Color: <strong>{esc(r['color']) or '—'}</strong><br>Location: <strong>{esc(r['location'])}</strong><br>
            Date: <strong>{fmt_date(r['date'])}</strong></div></div>"""

        ICON = {"match": ("✓", "#2f855a"), "partial": ("◐", "#dd6b20"),
                "differ": ("✗", "#c53030"), "unverified": ("?", "#a0aec0")}

        for s, lo, fo, rows in pairs:
            label = "Strong possible match" if s >= NOTIFY_THRESHOLD else "Weak possible match"
            checks = ""
            for x in rows:
                ic, col = ICON[x["state"]]
                checks += (f'<div style="display:flex;justify-content:space-between;gap:8px;padding:9px 0;'
                           f'border-bottom:1px solid #edf2f7;font-size:12px"><span><span style="color:{col};'
                           f'font-weight:800">{ic}</span>&nbsp; <strong>{x["rule"]}</strong>'
                           f'<span style="color:#718096">&nbsp;{x["detail"]}</span></span>'
                           f'<span style="color:{col};font-weight:700;white-space:nowrap">{x["result"]}</span></div>')
            notif_line = ""
            if f"{lo['id']}_{fo['id']}" in notified:
                notif_line = ('<div style="margin-top:10px;font-size:11px;color:#2a4365"><span class="sim-tag">SIMULATED</span>'
                              'In-app notification created for the owner of the lost report. No email was sent.</div>')
            with st.container(key=f"card_match_{lo['id']}_{fo['id']}"):
                html(f"""
                <div class="m-top">
                  {side('LOST REPORT', '#2b6cb0', lo)}
                  <div class="m-link">⛓</div>
                  {side('FOUND REPORT', '#2a9d8f', fo)}
                </div>
                <div class="m-bottom">
                  <div class="score-panel" style="width:240px;background:linear-gradient(160deg,#0e2a47,#1c525f);padding:24px;color:#fff;text-align:center">
                    <div style="font-size:10px;letter-spacing:1px;opacity:.7">SIMILARITY SCORE</div>
                    <div style="margin:14px auto;width:112px;height:112px;border-radius:50%;
                    background:conic-gradient(#2aa99b {s * 3.6}deg, rgba(255,255,255,.15) 0);
                    display:flex;align-items:center;justify-content:center">
                      <div style="width:90px;height:90px;border-radius:50%;background:#10345a;display:flex;
                      align-items:center;justify-content:center;flex-direction:column;font-weight:800">
                      <span style="font-size:28px;line-height:1">{s}</span>
                      <span style="font-size:11px;opacity:.7;font-weight:600">out of 100</span></div></div>
                    <div style="font-size:15px;font-weight:700">{label}</div>
                    <div style="font-size:10px;opacity:.7;margin-top:4px">Rule-based similarity of report details. Not a probability; not proof of ownership.</div>
                  </div>
                  <div style="flex:1;padding:18px 22px;background:#fff">
                    <div style="font-size:11px;font-weight:800;color:#2a9d8f">WHY THIS WAS MATCHED</div>
                    <div style="font-size:17px;margin-bottom:6px">Comparison breakdown</div>{checks}
                    <div style="font-size:10px;color:#a0aec0;margin-top:8px">✓ matches · ◐ partly matches · ✗ differs · ? not provided, so it earns no points</div>
                  </div>
                </div>
                <div style="margin-top:12px;background:#fffaf0;border:1px solid #fbd38d;color:#9c4221;
                padding:10px 14px;border-radius:8px;font-size:12px">⚠ Possible match only. Ownership must be
                verified at the campus Lost & Found office.</div>{notif_line}
                """)
            st.write("")
        if not pairs:
            html("""<div style="background:#fff;border:1px dashed #cbd5e0;border-radius:14px;padding:32px 20px;text-align:center">
            <div style="font-size:30px">🔎</div>
            <div style="font-size:15px;font-weight:700;color:#1a202c;margin-top:6px">No possible matches yet</div>
            <div style="font-size:12px;color:#718096;margin-top:4px;line-height:1.6">SmartFind compares every new lost and
            found report against the open reports on the other side.<br>Matches will appear here as soon as one is found.</div></div>""")

        if st.session_state.notifications:
            with st.expander(f"Notification log — simulated ({len(st.session_state.notifications)})"):
                st.caption("These are in-app records only. Email delivery is not implemented yet.")
                for n in reversed(st.session_state.notifications):
                    st.write(f"{n['time']} — \"{n['item']}\" · score {n['score']} · would notify {n['recipient']}")

    # ========================================================
    # ADMIN
    # ========================================================
    elif active == "Admin Management":
        html("""<div class="eyebrow">RECORD ADMINISTRATION</div><div class="page-title">Lost-and-Found Management</div>
        <div class="page-sub">Review, update, resolve, or delete item reports. Changes are reflected in Records and Possible Matches.</div>""")

        html("""<div class="warn-bar">🔓 <strong>Prototype only — no login.</strong> Admin Management has no
        authentication: anyone who can open this app can view, edit, resolve and delete every report.
        Fine for a classroom demo on a private network; <strong>not secure for real deployment</strong>
        (a real system needs sign-in and role checks).</div>""")

        reps = st.session_state.reports
        stats = [("ⓘ", "Total Lost Reports", sum(r["type"] == "Lost" for r in reps)),
                 ("⌕", "Total Found Reports", sum(r["type"] == "Found" for r in reps)),
                 ("⛓", "Possible Matches", len(current_pairs())),
                 ("✓", "Resolved Items", sum(r["status"] == "Resolved" for r in reps))]
        for col, (ic, lbl, val) in zip(st.columns(4), stats):
            with col:
                html(f"""<div style="background:#fff;border:1px solid #e2e8f0;border-radius:14px;padding:18px;
                display:flex;gap:14px;align-items:center;margin-bottom:8px"><div class="stat-icon">{ic}</div>
                <div><div style="font-size:11px;color:#718096">{lbl}</div>
                <div style="font-size:26px;font-weight:800;color:#0d2847">{val}</div></div></div>""")
        st.write("")

        @st.dialog("Update report")
        def update_dialog(rid):
            r = get_report(rid)
            if not r:
                return
            st.caption(f"{r['id']} — {r['item']}")
            statuses = ["Searching", "Resolved"] if r["type"] == "Lost" else ["Reported", "Resolved"]
            new_status = st.selectbox("Status", statuses,
                                      index=statuses.index(r["status"]) if r["status"] in statuses else 0)
            new_loc = st.text_input("Location", value=r["location"])
            if st.button("Save changes", **STRETCH):
                r["status"], r["location"] = new_status, new_loc.strip() or r["location"]
                save_report(r)
                st.rerun()

        @st.dialog("Delete report")
        def delete_dialog(rid):
            r = get_report(rid)
            if not r:
                return
            st.warning(f"Delete {r['id']} — {r['item']}? This can't be undone.")
            if st.button("Delete report", type="primary", **STRETCH):
                st.session_state.reports = [x for x in st.session_state.reports if x["id"] != rid]
                delete_report(rid)
                st.rerun()

        W = [2.6, 0.9, 1.3, 1.3, 0.8, 0.9, 0.9, 0.8]
        with st.container(key="card_admin"):
            st.markdown("**Recent reports**")
            with st.container(key="th_admin"):
                for col, h in zip(st.columns(W), ["ITEM", "TYPE", "DATE", "STATUS", "", "", "", ""]):
                    col.markdown(f'<span class="th">{h}</span>', unsafe_allow_html=True)
            if not reps:
                st.info("There are no reports yet.")
            for r in list(reps):
                rid = r["id"]
                with st.container(key=f"row_adm_{rid}"):
                    c = st.columns(W)
                    c[0].markdown(
                        f'<div style="display:flex;gap:10px;align-items:center">{thumb(r, 36)}<div>'
                        f'<strong>{esc(r["item"])}</strong><div class="rid">{rid}</div></div></div>',
                        unsafe_allow_html=True)
                    c[1].markdown(type_pill(r), unsafe_allow_html=True)
                    c[2].write(fmt_date(r["date"]))
                    c[3].write(r["status"])
                    if c[4].button("View", key=f"a_view_{rid}", **STRETCH):
                        show_details(rid)
                    if c[5].button("Update", key=f"a_upd_{rid}", **STRETCH):
                        update_dialog(rid)
                    if c[6].button("Resolve", key=f"a_res_{rid}", **STRETCH,
                                   disabled=r["status"] == "Resolved"):
                        r["status"] = "Resolved"
                        save_report(r)
                        st.rerun()
                    if c[7].button("Delete", key=f"a_del_{rid}", **STRETCH):
                        delete_dialog(rid)