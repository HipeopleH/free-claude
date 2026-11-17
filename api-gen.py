"""Ace Data Cloud -> free-claude-code key bridge."""
import io
import itertools
import json
import os
import platform
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # installed on demand by ensure_playwright()
    sync_playwright = None

# --- Configuration ---
TARGET_URL = "https://platform.acedata.cloud/documents/280928a2-2dce-419c-adb5-1ea835e8183a"
CREDIT_URL = "https://platform.acedata.cloud/documents/claude-chat-completions"
REGISTER_URL = "https://auth.acedata.cloud/auth/register?redirect=https://platform.acedata.cloud/auth/callback?redirect=%252Fdocuments%252Fclaude-chat-completions"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_FILE = os.path.join(BASE_DIR, "assets.txt")
ACCOUNTS_FILE = os.path.join(BASE_DIR, "accounts.txt")
KEYS_FILE = os.path.join(BASE_DIR, "keys.txt")
ACCOUNT_HISTORY = 3
FCC_ADMIN_URL = "http://127.0.0.1:8082/admin"
FCC_INSTALL_CMD = (
    '& ([scriptblock]::Create((irm '
    '"https://raw.githubusercontent.com/Alishahryar1/free-claude-code/main/scripts/install.ps1")))'
)
PROVIDER_NAME = "ace data cloud"
PROVIDER_BASE_URL = "https://api.acedata.cloud/v1"
BRIDGE_PORT = 8080
MAX_INSTANCES = 10
START_DELAY = 45          # seconds between browser starts, so Gmail codes never overlap

# Shared files (accounts.txt / keys.txt) and the shared admin panel are written
# by every instance, so each of them gets a single lock.
FILE_LOCK = threading.RLock()
FCC_LOCK = threading.RLock()

# Run plan: every step advances the bar, weighted by how long it really takes.
STEPS = (
    ("Check dependencies", 3),
    ("Load configuration", 1),
    ("Start Gmail bridge", 2),
    ("Check admin panel", 3),
    ("Open registration page", 2),
    ("Request verification code", 2),
    ("Receive verification code", 6),
    ("Fill registration form", 1),
    ("Submit registration", 3),
    ("Open console", 2),
    ("Open API key dialog", 2),
    ("Capture API key", 3),
    ("Save key to keys.txt", 1),
    ("Open provider settings", 2),
    ("Enter API key", 3),
    ("Save provider", 3),
    ("Apply changes", 2),
    ("Claim free credit", 3),
    ("Open Dedicated Balance", 1),
)
TOTAL_STEPS = len(STEPS)
STEP_WEIGHT = dict(STEPS)

# Setup runs once; everything after it runs inside every instance.
WORKER_STEPS = tuple((title, weight) for title, weight in STEPS[3:])

# --- Terminal UI ---
def _enable_windows_ansi():
    if os.name != "nt":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-11)
        m = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(m)):
            k.SetConsoleMode(h, m.value | 0x0004)
    except Exception:
        pass

def _console_utf8():
    """Best-effort switch of the current console to UTF-8 so the glyphs render."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _encodable(text):
    try:
        text.encode(sys.stdout.encoding or "utf-8")
        return True
    except Exception:
        return False


_GLYPHS = ("\u2588\u2591\u25b8\u2714\u2716\u203a\u2022\u2026\u2192\u2500\u2502"
           "\u250c\u2510\u2514\u2518\u25cf\u25cb\u25b6\u21b3\u2584\u2580\u280b")
if not _encodable(_GLYPHS):
    _console_utf8()
if not _encodable(_GLYPHS):
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

try:
    IS_TTY = bool(sys.stdout.isatty())
except Exception:
    IS_TTY = False

COLOR = IS_TTY and not os.environ.get("NO_COLOR")
if COLOR:
    _enable_windows_ansi()
LIVE = IS_TTY

UNICODE = _encodable(_GLYPHS)
if UNICODE:
    BAR_FULL, BAR_EMPTY = "\u2588", "\u2591"
    S_OK, S_ERR = "\u2714", "\u2716"
    S_WARN, S_INFO, S_NOTE, S_ARROW, S_ELL = "!", "\u203a", "\u2022", "\u25b8", "\u2026"
    S_DOT, S_RING, S_PLAY, S_HINT = "\u25cf", "\u25cb", "\u25b6", "\u21b3"
    SPIN = ("\u280b", "\u2819", "\u2839", "\u2838", "\u283c",
            "\u2834", "\u2826", "\u2827", "\u2807", "\u280f")
    BOX = {"tl": "\u250c", "tr": "\u2510", "bl": "\u2514", "br": "\u2518",
           "h": "\u2500", "v": "\u2502"}
else:
    BAR_FULL, BAR_EMPTY = "#", "-"
    S_OK, S_ERR, S_WARN, S_INFO, S_NOTE, S_ARROW, S_ELL = "OK", "X", "!", ">", "*", ">", "..."
    S_DOT, S_RING, S_PLAY, S_HINT = "@", "o", ">", "->"
    SPIN = ("|", "/", "-", "\\")
    BOX = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}

RULE_W = 56

# One accent colour per instance, so a row is recognisable at a glance.
ACCENTS = (45, 213, 220, 84, 208, 111, 141, 156, 203, 51)

class C:
    RESET = "\033[0m" if COLOR else ""
    BOLD = "\033[1m" if COLOR else ""
    DIM = "\033[2m" if COLOR else ""
    CYAN = "\033[38;5;51m" if COLOR else ""
    BLUE = "\033[38;5;39m" if COLOR else ""
    PURPLE = "\033[38;5;141m" if COLOR else ""
    GREEN = "\033[38;5;46m" if COLOR else ""
    YELLOW = "\033[38;5;220m" if COLOR else ""
    RED = "\033[38;5;203m" if COLOR else ""
    GREY = "\033[38;5;244m" if COLOR else ""
    WHITE = "\033[38;5;255m" if COLOR else ""
    LINE = "\033[38;5;240m" if COLOR else ""

def accent(code):
    """The colour that belongs to one instance."""
    return f"\033[38;5;{code}m" if COLOR else ""

def _clip(text, width):
    """Shortens plain text with an ellipsis so a row can never wrap."""
    text = text or ""
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= len(S_ELL):
        return text[:width]
    return text[:width - len(S_ELL)] + S_ELL

def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec:02d}s" if minutes else f"{sec}s"

def mask_key(key):
    return key if len(key) <= 12 else f"{key[:4]}{S_ELL}{key[-4:]}"

class WorkerRow:
    """One browser instance: its live step, its colour and how it ended."""

    def __init__(self, number, accent_code=45):
        self.number = number
        self.accent = accent_code      # 256-colour code that identifies this instance
        self.status = "queued"        # queued | working | done | failed | cancelled
        self.done = 0
        self.total = 1
        self.weight_done = 0
        self.fill = 0.0
        self.title = "queued"
        self.note = ""
        self.started_at = None
        self.finished_at = None
        self.email = ""
        self.key = ""
        self.message = ""

    def elapsed(self):
        if self.started_at is None:
            return 0.0
        return (self.finished_at or time.time()) - self.started_at


class UI:
    """Live dashboard: a setup checklist plus one colour-coded row per browser."""

    def __init__(self):
        self.lock = threading.RLock()
        self.input_lock = threading.Lock()
        self._tls = threading.local()
        self.plan = [("", 1)]
        self.total_weight = 1
        self.workers = []
        self.setup_rows = []
        self.count = 1
        self.tag_w = 1
        self.started_at = time.time()
        self.started = False
        self.live = LIVE
        self.block_rows = 0
        try:
            self.cols = max(60, min(shutil.get_terminal_size((100, 24)).columns, 140))
        except Exception:
            self.cols = 100
        self.bar_w = 22

    # --- geometry ----------------------------------------------------------
    def _width(self):
        return max(44, min(getattr(self, "cols", 100) - 4, 92))

    def _bar_w(self):
        width = self._width()
        if width >= 80:
            return 22
        if width >= 68:
            return 16
        return 12

    # --- lifecycle ---------------------------------------------------------
    def begin(self, count=1, plan=None):
        """Opens the dashboard: one live row per browser instance.

        `begin(plan)` keeps the old single-instance call shape.
        """
        with self.lock:
            if isinstance(count, int) or count is None:
                self.count = max(1, min(MAX_INSTANCES, int(count or 1)))
            else:
                plan, self.count = count, 1

            if plan is None:
                self.plan = [("", 1)]
            else:
                self.plan = [(str(t), max(1, int(w))) for t, w in plan] or [("", 1)]
            self.total_weight = sum(w for _, w in self.plan) or 1

            self.tag_w = max(1, len(str(self.count)))
            self.workers = [
                WorkerRow(i + 1, ACCENTS[i % len(ACCENTS)]) for i in range(self.count)
            ]
            for worker in self.workers:
                worker.total = len(self.plan)

            self.started = True
            self.started_at = time.time()
            self.block_rows = 0
            self._banner()
            self._paint()

    def bind(self, index):
        """Points this thread's steps and log lines at one instance."""
        self._tls.index = None if index is None else int(index)

    def bound(self):
        return getattr(self._tls, "index", None)

    def worker(self, index):
        with self.lock:
            if 0 <= index < len(self.workers):
                return self.workers[index]
            return None

    def worker_start(self, index):
        with self.lock:
            worker = self.worker(index)
            if worker is None:
                return
            worker.status = "working"
            worker.started_at = time.time()
            worker.title = "Starting the browser"
            worker.note = ""
            self._paint()

    def worker_done(self, index, ok, message=""):
        with self.lock:
            worker = self.worker(index)
            if worker is None:
                return
            worker.status = "done" if ok else "failed"
            worker.finished_at = time.time()
            worker.message = message or ""
            worker.note = ""
            if ok:
                worker.fill = 1.0
                worker.done = worker.total
                worker.weight_done = self.total_weight
            self._paint()

    def worker_email(self, index, email):
        with self.lock:
            worker = self.worker(index)
            if worker is not None:
                worker.email = email or ""
                self._paint()

    def worker_key(self, index, key):
        with self.lock:
            worker = self.worker(index)
            if worker is not None:
                worker.key = key or ""
                self._paint()

    def queue_note(self, index, text):
        with self.lock:
            worker = self.worker(index)
            if worker is not None and worker.status == "queued":
                worker.note = text or ""
                self._paint()

    def skip_queued(self):
        with self.lock:
            for worker in self.workers:
                if worker.status == "queued":
                    worker.status = "cancelled"
                    worker.note = ""
            self._paint()

    # --- setup checklist ---------------------------------------------------
    def setup_begin(self, items):
        with self.lock:
            self.setup_rows = [
                {"label": label, "state": "pending", "detail": detail or "waiting", "hint": ""}
                for label, detail in items
            ]
            if not self.live:
                print("  -- setup ------------------------------", flush=True)
                return
            self._paint()

    def setup_item(self, label, state, detail="", hint=""):
        """Moves one checklist row between pending / busy / ok / warn / fail."""
        with self.lock:
            item = next((i for i in self.setup_rows if i["label"] == label), None)
            if item is None:
                item = {"label": label, "state": "pending", "detail": "", "hint": ""}
                self.setup_rows.append(item)
            item["state"] = state
            if detail:
                item["detail"] = detail
            item["hint"] = hint or ""

            if not self.live:
                if state == "fail":
                    print(f"  [XX] {label}  {item['detail']}", flush=True)
                    if hint:
                        print(f"       {hint}", flush=True)
                elif state == "warn":
                    print(f"  [!!] {label}  {item['detail']}", flush=True)
                elif state == "ok":
                    print(f"  [OK] {label}  {item['detail']}", flush=True)
                return
            self._paint()

    def setup_end(self):
        with self.lock:
            if self.live and self.started and self.setup_rows:
                # Leave the finished checklist in the scrollback, then keep only
                # the dashboard live so the screen does not keep growing.
                frozen = self._setup_lines()
                self._erase()
                for line in frozen:
                    print(line, flush=True)
                self.setup_rows = []
                self._paint()
                return
            self.setup_rows = []
            self._paint()

    # --- progress ----------------------------------------------------------
    def _current(self):
        index = getattr(self._tls, "index", None)
        if index is None or not (0 <= index < len(self.workers)):
            return None
        return self.workers[index]

    def step(self, title):
        with self.lock:
            worker = self._current()
            if worker is None:
                if self.started:
                    self._print(f"      {title}")
                return
            if not self.started:
                return

            weight = 1
            for i in range(worker.done, len(self.plan)):
                if self.plan[i][0] == title:
                    weight = self.plan[i][1]
                    break
            worker.done = min(worker.total, worker.done + 1)
            worker.weight_done = min(self.total_weight, worker.weight_done + weight)
            worker.title = title
            worker.note = ""
            target = worker.weight_done / self.total_weight

            if not self.live:
                worker.fill = target
                pct = int(round(target * 100))
                print(f"  #{worker.number:02d}  {worker.done:02d}/{worker.total:02d}  "
                      f"{pct:3d}%  {title}", flush=True)
                return

            start = worker.fill
            frames = 5 if self.count == 1 else 3
            for k in range(1, frames + 1):
                worker.fill = start + (target - start) * (k / float(frames))
                self._paint()
                time.sleep(0.006)
            worker.fill = target
            self._paint()

    def set_note(self, text):
        with self.lock:
            worker = self._current()
            if worker is None:
                return
            worker.note = text or ""
            self._paint()

    # --- log lines ---------------------------------------------------------
    def ok(self, msg):
        self._icon(S_OK, C.GREEN, msg)

    def info(self, msg):
        self._icon(S_INFO, C.BLUE, msg)

    def warn(self, msg):
        self._icon(S_WARN, C.YELLOW, msg)

    def err(self, msg):
        self._icon(S_ERR, C.RED, msg)

    def detail(self, msg):
        with self.lock:
            self._print(f"      {C.DIM}{msg}{C.RESET}" if COLOR else f"      {msg}")

    def cmd(self, text):
        with self.lock:
            if COLOR:
                self._print(f"        {C.CYAN}{C.BOLD}{text}{C.RESET}")
            else:
                self._print(f"        {text}")

    def blank(self):
        with self.lock:
            self._print("")

    def key(self, value, title="API KEY", note=""):
        value = (value or "").strip()
        if not value:
            return
        with self.lock:
            worker = self._current()
            accent_code = worker.accent if worker else None
            if worker is not None:
                title = f"{title}  {S_NOTE}  #{worker.number:0{self.tag_w}d}"
            room = max(16, self.cols - 6)
            chunks = [value[i:i + room] for i in range(0, len(value), room)]
            inner = max(len(c) for c in chunks) + 2
            inner = max(inner, len(title) + 6)

            if UNICODE:
                top = "\u250c\u2500 " + title + " " + "\u2500" * (inner - len(title) - 3) + "\u2510"
                bottom = "\u2514" + "\u2500" * inner + "\u2518"
                bar = "\u2502"
            else:
                top = "+- " + title + " " + "-" * (inner - len(title) - 3) + "+"
                bottom = "+" + "-" * inner + "+"
                bar = "|"

            frame = accent(accent_code) if (COLOR and accent_code) else C.PURPLE
            self._print(f"  {frame}{top}{C.RESET}")

            for chunk in chunks:
                pad = " " * (inner - len(chunk) - 2)
                if COLOR:
                    self._print(f"  {frame}{bar}{C.RESET} {C.GREEN}{C.BOLD}{chunk}{C.RESET}{pad} "
                                f"{frame}{bar}{C.RESET}")
                else:
                    self._print(f"  {bar} {chunk}{pad} {bar}")

            self._print(f"  {frame}{bottom}{C.RESET}" if COLOR else f"  {bottom}")
            if note:
                self.detail(note)

    # --- prompts and static cards -----------------------------------------
    def card(self, title, lines):
        """A static framed hint box that stays in the scrollback."""
        with self.lock:
            width = self._width()
            out = [self._top(title, "", width)]
            for line in lines:
                text = _clip(line, width - 3)
                out.append(self._row([(" ", " "), (text, f"{C.GREY}{text}{C.RESET}")], width))
            out.append(self._bottom(width))
            self._print_lines(out)

    def ask(self, prompt):
        # One prompt at a time, and no worker may redraw the block underneath it.
        with self.input_lock:
            with self.lock:
                self._erase()
                if COLOR:
                    sys.stdout.write(f"  {C.PURPLE}{C.BOLD}?{C.RESET}  {C.WHITE}{prompt}{C.RESET}  ")
                else:
                    sys.stdout.write(f"  ?  {prompt}  ")
                sys.stdout.flush()
                try:
                    answer = input()
                except EOFError:
                    answer = ""
                if self.started:
                    self._paint()
        return answer.strip()

    def phase(self, title):
        with self.lock:
            pad = max(0, RULE_W - 4 - len(title))
            if COLOR:
                line = (f"  {C.PURPLE}{C.BOLD}{title.lower()}{C.RESET} "
                        f"{C.LINE}{BOX['h'] * pad}{C.RESET}")
            else:
                line = f"  -- {title} " + "-" * pad
            self._print(line)

    def stop(self):
        with self.lock:
            self._erase()

    def finish(self, success, message=""):
        with self.lock:
            self._erase()
            self.started = False
            self.setup_rows = []
            elapsed = _fmt_duration(time.time() - self.started_at)
            width = self._width()
            done = [w for w in self.workers if w.status == "done"]

            if success:
                mark, mcol, verdict = S_OK, C.GREEN, "COMPLETE"
            elif done:
                mark, mcol, verdict = S_WARN, C.YELLOW, "PARTIAL"
            else:
                mark, mcol, verdict = S_ERR, C.RED, "STOPPED"

            out = [""]
            right = f"{len(done)} of {len(self.workers)} saved"
            out.append(self._top("results", right, width))
            for worker in self.workers:
                out.append(self._result_row(worker, width))
            out.append(self._bottom(width))

            if COLOR:
                out.append(f"  {mcol}{C.BOLD}{mark}  {verdict}{C.RESET}"
                           f"   {C.DIM}{elapsed}{C.RESET}")
            else:
                out.append(f"  [{mark}]  {verdict}   -  {elapsed}")
            if message:
                out.append(f"  {C.GREY}{message}{C.RESET}" if COLOR else f"  {message}")
            if done:
                tail = (f"keys appended to {os.path.basename(KEYS_FILE)}"
                        f"  {S_NOTE}  accounts keep the last {ACCOUNT_HISTORY}")
                out.append(f"  {C.DIM}{tail}{C.RESET}" if COLOR else f"  {tail}")
            out.append("")
            self._print_lines(out)

    # --- row rendering -----------------------------------------------------
    def _result_row(self, worker, width):
        tag = f"#{worker.number:0{self.tag_w}d}"
        chip_plain = f" {tag} "
        chip_styled = self._chip(worker.number, worker.accent)
        time_w = 8

        if worker.status == "done":
            mark, mcol = S_OK, C.GREEN
            tail, tcol = worker.key or "key captured", C.GREEN
            took = _fmt_duration(worker.elapsed())
        elif worker.status == "failed":
            mark, mcol = S_ERR, C.RED
            tail, tcol = worker.message or "failed", C.RED
            took = _fmt_duration(worker.elapsed())
        elif worker.status == "cancelled":
            mark, mcol = S_WARN, C.YELLOW
            tail, tcol = "cancelled", C.YELLOW
            took = "-"
        elif worker.status == "working":
            mark, mcol = S_DOT, C.YELLOW
            tail, tcol = "still running", C.YELLOW
            took = _fmt_duration(worker.elapsed())
        else:
            mark, mcol = S_RING, C.GREY
            tail, tcol = "never started", C.GREY
            took = "-"

        lead = 1 + 1 + 1 + len(chip_plain) + 2 + time_w + 2
        room = max(8, width - lead - 1)
        tail = _clip(tail, room)
        took = f"{_clip(took, time_w):>{time_w}}"
        return self._row([
            (" ", " "),
            (mark, f"{mcol}{mark}{C.RESET}"),
            (" ", " "),
            (chip_plain, chip_styled),
            ("  ", "  "),
            (took, f"{C.DIM}{took}{C.RESET}"),
            ("  ", "  "),
            (tail, f"{tcol}{tail}{C.RESET}"),
            (" ", " "),
        ], width)

    def _dash_row(self, worker, width):
        tag = f"#{worker.number:0{self.tag_w}d}"
        chip_plain = f" {tag} "
        chip_styled = self._chip(worker.number, worker.accent)
        bar_w = self._bar_w()
        pct_w = 4
        right_w = 8
        acc = accent(worker.accent)
        elapsed = _fmt_duration(worker.elapsed())
        title = note = ""
        tcol = ncol = C.WHITE
        right = ""
        fill = worker.fill
        bcol = acc

        if worker.status == "queued":
            mark, mcol = S_RING, C.GREY
            fill, bcol, pcol = 0.0, C.LINE, C.GREY
            title, tcol = worker.note or "waiting for a slot", C.GREY
        elif worker.status == "done":
            mark, mcol = S_OK, C.GREEN
            fill, bcol, pcol = 1.0, C.GREEN, C.GREEN
            title, tcol = "key saved", C.GREEN
            note, ncol = mask_key(worker.key), C.DIM
            right = elapsed
        elif worker.status == "failed":
            mark, mcol = S_ERR, C.RED
            fill, bcol, pcol = worker.fill, C.RED, C.RED
            title, tcol = worker.message or "failed", C.RED
            right = elapsed
        elif worker.status == "cancelled":
            mark, mcol = S_WARN, C.YELLOW
            fill, bcol, pcol = 0.0, C.LINE, C.YELLOW
            title, tcol = "cancelled", C.YELLOW
        else:
            mark, mcol = S_DOT, acc
            pcol = C.WHITE
            title, tcol = worker.title or "", C.WHITE
            note, ncol = worker.note or "", C.DIM
            right = elapsed

        # " mark " chip " bar " pct "  " middle right "  -> 12 + chip + bar + right
        fixed = 3 + len(chip_plain) + 1 + bar_w + 1 + pct_w + 2 + right_w + 1
        room = max(6, width - fixed)

        title = _clip(title, room)
        if note:
            note = _clip(note, max(0, room - len(title) - 2))
        mid_plain = title + (("  " + note) if note else "")
        mid_plain += " " * max(0, room - len(mid_plain))
        mid_styled = f"{tcol}{title}{C.RESET}"
        if note:
            mid_styled += "  " + f"{ncol}{note}{C.RESET}"
        mid_styled += " " * max(0, room - len(title) - (len(note) + 2 if note else 0))

        pos = int(round(bar_w * max(0.0, min(1.0, fill))))
        bar_plain = BAR_FULL * pos + BAR_EMPTY * (bar_w - pos)
        bar_styled = f"{bcol}{BAR_FULL * pos}{C.LINE}{BAR_EMPTY * (bar_w - pos)}{C.RESET}"
        pct = int(round(100 * max(0.0, min(1.0, fill))))
        pct_plain = f"{pct:3d}%"
        right_plain = f"{_clip(right, right_w):>{right_w}}"

        return self._row([
            (" ", " "),
            (mark, f"{mcol}{mark}{C.RESET}"),
            (" ", " "),
            (chip_plain, chip_styled),
            (" ", " "),
            (bar_plain, bar_styled),
            (" ", " "),
            (pct_plain, f"{pcol}{pct_plain}{C.RESET}"),
            ("  ", "  "),
            (mid_plain, mid_styled),
            (right_plain, f"{C.DIM}{right_plain}{C.RESET}"),
            (" ", " "),
        ], width)

    def _row_line(self, worker):
        """The row for one instance, already fitted to the terminal width."""
        return self._dash_row(worker, self._width())

    # --- boxes -------------------------------------------------------------
    def _row(self, segs, width):
        plain = "".join(p for p, _ in segs)
        styled = "".join(s for _, s in segs)
        if len(plain) > width:
            plain = plain[:width]
            styled = plain
        styled += " " * max(0, width - len(plain))
        if COLOR:
            return f"  {C.LINE}{BOX['v']}{C.RESET}{styled}{C.LINE}{BOX['v']}{C.RESET}"
        return f"  {BOX['v']}{styled}{BOX['v']}"

    def _top(self, title, right, width):
        h = BOX["h"]
        title = _clip(title, max(3, width // 3))
        right = _clip(right or "", max(0, width - len(title) - 9))
        left_txt = f"{h} {title} "
        right_txt = f" {right} " if right else ""
        fill = width - len(left_txt) - len(right_txt)
        if fill < 1:
            right_txt = ""
            fill = max(1, width - len(left_txt))
        if COLOR:
            return (f"  {C.LINE}{BOX['tl']}{C.RESET}"
                    f"{C.GREY}{h}{C.RESET} {C.PURPLE}{C.BOLD}{title}{C.RESET} "
                    f"{C.LINE}{h * fill}{C.RESET}"
                    f"{C.DIM}{right_txt}{C.RESET}"
                    f"{C.LINE}{BOX['tr']}{C.RESET}")
        return f"  {BOX['tl']}{left_txt}{h * fill}{right_txt}{BOX['tr']}"

    def _bottom(self, width):
        h = BOX["h"]
        if COLOR:
            return f"  {C.LINE}{BOX['bl']}{h * width}{BOX['br']}{C.RESET}"
        return f"  {BOX['bl']}{h * width}{BOX['br']}"

    def _setup_lines(self):
        width = self._width()
        out = [self._top("setup", f"{sum(1 for i in self.setup_rows if i['state'] == 'ok')}"
                                  f" of {len(self.setup_rows)} ready", width)]
        for item in self.setup_rows:
            state = item["state"]
            if state == "busy":
                icon, icol, lcol = self._spin_icon(), C.YELLOW, C.WHITE
            elif state == "ok":
                icon, icol, lcol = S_OK, C.GREEN, C.WHITE
            elif state == "warn":
                icon, icol, lcol = S_WARN, C.YELLOW, C.WHITE
            elif state == "fail":
                icon, icol, lcol = S_ERR, C.RED, C.RED
            else:
                icon, icol, lcol = S_RING, C.GREY, C.GREY

            label = _clip(item["label"], 18)
            detail = _clip(item["detail"], max(0, width - 26))
            out.append(self._row([
                (" ", " "),
                (icon, f"{icol}{icon}{C.RESET}"),
                ("  ", "  "),
                (f"{label:<18}", f"{lcol}{label:<18}{C.RESET}"),
                ("  ", "  "),
                (detail, f"{C.DIM}{detail}{C.RESET}"),
            ], width))
            if state == "fail" and item.get("hint"):
                hint = _clip(item["hint"], max(0, width - 9))
                out.append(self._row([
                    (" ", " "),
                    ("   ", "   "),
                    (S_HINT + " ", f"{C.RED}{S_HINT}{C.RESET} "),
                    (hint, f"{C.DIM}{hint}{C.RESET}"),
                ], width))
        out.append(self._bottom(width))
        return out

    def _dash_lines(self):
        width = self._width()
        done = sum(1 for w in self.workers if w.status == "done")
        out = [self._top("instances", f"{done} of {len(self.workers)} done", width)]
        for worker in self.workers:
            out.append(self._dash_row(worker, width))
        out.append(self._bottom(width))
        return out

    def _block_lines(self):
        lines = []
        if self.setup_rows:
            lines.extend(self._setup_lines())
        if self.started and self.workers:
            if lines:
                lines.append("")
            lines.extend(self._dash_lines())
        return lines

    # --- banner ------------------------------------------------------------
    def _banner(self):
        width = self._width()
        out = [""]
        if UNICODE:
            out.append(f"{C.CYAN}{C.BOLD}  █▀▀ █▀█ █▀▀ █▀▀   █▀▀ █   ▄▀█ █ █ █▀▄ █▀▀{C.RESET}")
            out.append(f"{C.BLUE}{C.BOLD}  █▀  █▀▄ ██▄ ██▄   █▄▄ █▄▄ █▀█ █▄█ █▄▀ ██▄{C.RESET}")
        else:
            out.append(f"  {C.CYAN}{C.BOLD}FREE CLAUDE{C.RESET}")

        # The tagline sits inside the rule so the banner lines up with the boxes.
        label = " headless key generator "
        fill = max(2, width + 2 - len(label) - 2)
        if COLOR:
            out.append(f"  {C.LINE}{BOX['h'] * 2}{C.RESET}"
                       f"{C.PURPLE}{C.BOLD}{label}{C.RESET}"
                       f"{C.LINE}{BOX['h'] * fill}{C.RESET}")
        else:
            out.append(f"  {BOX['h'] * 2}{label}{BOX['h'] * fill}")
        bits = [f"{self.count} browser" + ("s" if self.count > 1 else "")]
        if self.count > 1:
            bits.append(f"starts {START_DELAY}s apart")
        bits.append(f"keys land in {os.path.basename(KEYS_FILE)}")
        tail = _clip(f" {S_NOTE} ".join(bits), width + 2)
        out.append(f"  {C.GREY}{tail}{C.RESET}")
        out.append("")
        self._erase()
        for line in out:
            print(line, flush=True)

    # --- painting ----------------------------------------------------------
    def _erase(self):
        if not self.live or not self.block_rows:
            return
        out = ["\r\033[2K\033[1A"] * max(0, self.block_rows - 1) + ["\r\033[2K"]
        sys.stdout.write("".join(out))
        sys.stdout.flush()
        self.block_rows = 0

    def _paint(self):
        if not self.live or not self.started:
            return
        lines = self._block_lines()
        self._erase()
        if not lines:
            return
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
        self.block_rows = len(lines)

    def _print_lines(self, lines):
        self._erase()
        for line in lines:
            print(line, flush=True)
        self._paint()

    def _print(self, text):
        self._print_lines([text])

    def _tag(self):
        worker = self._current()
        if worker is None:
            return ""
        return self._chip(worker.number, worker.accent)

    def _chip(self, number, accent_code=45):
        tag = f"#{number:0{self.tag_w}d}"
        if not COLOR:
            return f" {tag} "
        return f"\033[48;5;{accent_code}m\033[38;5;232m{C.BOLD} {tag} {C.RESET}"

    def _icon(self, icon, color, msg):
        with self.lock:
            tag = self._tag()
            lead = f"{tag}  " if tag else ""
            if COLOR:
                self._print(f"  {lead}{color}{icon}{C.RESET}  {msg}")
            else:
                self._print(f"  {lead}[{icon}]  {msg}")

    def _spin_icon(self):
        return SPIN[int(time.time() * 12) % len(SPIN)]


ui = UI()

# --- Dependency bootstrap ---
SCRIPT_DIR = BASE_DIR
CFT_KNOWN_GOOD = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)


def _pkg_version(name):
    try:
        from importlib import metadata
        return metadata.version(name)
    except Exception:
        return "installed"


def _stream(cmd, prefix, item=None):
    """Runs a command and shows its live tail in the checklist (or a worker row)."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines = queue.Queue()

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        except Exception:
            pass
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True).start()

    last = ""
    shown = 0.0
    code = None
    while True:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            line = ""
        if line is None:
            code = proc.wait()
            break
        if line:
            line = line.strip()
            if line:
                last = line[:66]
        if item:
            ui.setup_item(item, "busy", (prefix + ": " + last) if last else (prefix + "..."))
        elif ui.bound() is not None:
            ui.set_note(f"{prefix}: {last}" if last else prefix)
        elif last:
            now = time.time()
            if now - shown >= 1.0:
                ui.detail(f"{prefix}: {last}")
                shown = now
    if item:
        ui.setup_item(item, "busy", (prefix + ": " + last) if last else prefix)
    elif ui.bound() is not None:
        ui.set_note("")
    return code, last


def _ensure_pip():
    probe = subprocess.run(
        [sys.executable, "-m", "pip", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if probe.returncode == 0:
        return
    ui.setup_item("Python packages", "busy", "pip is missing - setting it up")
    ui.warn("pip is missing — bootstrapping it with ensurepip.")
    code, tail = _stream([sys.executable, "-m", "ensurepip", "--upgrade"],
                         "ensurepip", "Python packages")
    if code != 0:
        raise Exception(f"Could not bootstrap pip. Last output: {tail or 'unknown error'}")


def ensure_playwright():
    global sync_playwright
    if sync_playwright is not None:
        ui.setup_item("Python packages", "ok", f"playwright {_pkg_version('playwright')}")
        return
    ui.setup_item("Python packages", "busy", "installing playwright (one time)")
    ui.detail("Playwright is missing - installing it for you. This only happens once.")
    _ensure_pip()
    code, tail = _stream(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--progress-bar", "off", "playwright"],
        "pip", "Python packages",
    )
    if code != 0:
        ui.setup_item("Python packages", "fail", "pip could not install it",
                      hint="run it yourself:  python -m pip install playwright")
        raise Exception(f"pip could not install Playwright. Last output: {tail or 'unknown error'}")

    import importlib
    importlib.invalidate_caches()
    from playwright.sync_api import sync_playwright as _sync_playwright
    sync_playwright = _sync_playwright
    ui.setup_item("Python packages", "ok", f"playwright {_pkg_version('playwright')} installed")


def ensure_chromium():
    if sync_playwright is None:
        ui.setup_item("Chromium browser", "warn", "skipped - no Playwright")
        return
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        if exe and os.path.exists(exe):
            ui.setup_item("Chromium browser", "ok", "ready")
            return
    except Exception:
        pass

    ui.setup_item("Chromium browser", "busy", "downloading - about 150 MB, one time")
    ui.detail("Chromium is missing - downloading it now (about 150 MB, one time).")
    code, tail = _stream([sys.executable, "-m", "playwright", "install", "chromium"],
                         "chromium", "Chromium browser")
    if code != 0:
        ui.setup_item("Chromium browser", "fail", "download failed",
                      hint="run it yourself:  python -m playwright install chromium")
        raise Exception(f"Could not download Chromium. Last output: {tail or 'unknown error'}")
    ui.setup_item("Chromium browser", "ok", "downloaded")


def _cft_platform():
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform == "darwin":
        return "mac-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def ensure_chromedriver():
    found = shutil.which("chromedriver")
    if found:
        ui.setup_item("chromedriver", "ok", "already on PATH")
        return found

    target_dir = os.path.join(SCRIPT_DIR, "drivers")
    binary = "chromedriver.exe" if os.name == "nt" else "chromedriver"
    local = os.path.join(target_dir, binary)

    if os.path.exists(local):
        ui.setup_item("chromedriver", "ok", "found in drivers/")
    else:
        ui.setup_item("chromedriver", "busy", "fetching the stable build")
        try:
            plat = _cft_platform()
            with urllib.request.urlopen(CFT_KNOWN_GOOD, timeout=30) as resp:
                meta = json.load(resp)
            url = next(
                d["url"]
                for d in meta["channels"]["Stable"]["downloads"]["chromedriver"]
                if d["platform"] == plat
            )
            with urllib.request.urlopen(url, timeout=120) as resp:
                blob = resp.read()
            os.makedirs(target_dir, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                member = next(n for n in zf.namelist() if n.endswith(binary))
                with zf.open(member) as src, open(local, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if os.name != "nt":
                os.chmod(local, 0o755)
            ui.setup_item("chromedriver", "ok", "installed to drivers/")
        except Exception as e:
            ui.setup_item("chromedriver", "warn", f"optional - skipped ({e.__class__.__name__})",
                          hint="only other tools need it; the run works without it")
            return None

    os.environ["PATH"] = target_dir + os.pathsep + os.environ.get("PATH", "")
    return local


def ensure_environment():
    ensure_playwright()
    ensure_chromium()
    ensure_chromedriver()


# --- Localhost Server State for Gmail Tampermonkey Bridge ---
RECEIVED_CODES = []
server_lock = threading.Lock()

class CodeReceiverHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        with server_lock:
            if not RECEIVED_CODES:
                self.wfile.write(b"No verification codes received yet.\n")
            else:
                self.wfile.write(b"--- RECEIVED VERIFICATION CODES ---\n\n")
                for item in RECEIVED_CODES:
                    self.wfile.write(f"Code: {item['code']} | Time: {item['time']}\n".encode('utf-8'))

    def do_POST(self):
        global RECEIVED_CODES
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data.decode('utf-8'))
            code = data.get('code')
            if code:
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                owner = (data.get('email') or data.get('to') or data.get('recipient') or '')
                with server_lock:
                    RECEIVED_CODES.append({
                        "code": str(code),
                        "time": timestamp,
                        "ts": time.time(),
                        "email": str(owner).strip().lower(),
                    })
                ui.ok(f"Verification code received via bridge: {code}")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Code received successfully!")
                return
        except Exception as e:
            ui.err(f"Error parsing bridge payload: {e}")
        self.send_response(400)
        self.end_headers()
        
    def log_message(self, format, *args):
        return

def start_local_server(port=BRIDGE_PORT):
    server = HTTPServer(("localhost", port), CodeReceiverHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

CONSUMED_CODES = set()

def wait_for_bridge_code(timeout_s=90, email=""):
    """Oldest unconsumed code, preferring one the bridge tagged for `email`.

    The userscript only sends the code, but an `email`/`to` field is honoured
    when present, so a smarter bridge can pin the code to the right instance.
    """
    email = (email or "").strip().lower()
    start = time.time()
    last = -1
    while time.time() - start < timeout_s:
        with server_lock:
            for item in RECEIVED_CODES:
                if item["code"] in CONSUMED_CODES:
                    continue
                owner = (item.get("email") or "").lower()
                if email and owner and owner != email:
                    continue
                CONSUMED_CODES.add(item["code"])
                ui.set_note("")
                return item["code"]
        elapsed = int(time.time() - start)
        if elapsed != last:
            last = elapsed
            ui.set_note(f"Gmail {elapsed}s / {timeout_s}s")
        time.sleep(0.3)
    ui.set_note("")
    return None

def discard_late_codes(older_than=120):
    """Drops codes nobody claimed. Recent ones are left alone: another
    instance may still be waiting for its own code."""
    cutoff = time.time() - max(0, older_than)
    with server_lock:
        late = [item for item in RECEIVED_CODES
                if item["code"] not in CONSUMED_CODES and item.get("ts", 0) < cutoff]
    for item in late:
        CONSUMED_CODES.add(item["code"])

# --- Account emails, passwords and API keys ---
def _read_email_lines(path):
    lines = []
    try:
        if not os.path.exists(path):
            return lines
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or "@" not in line or "=" in line or line.startswith("http"):
                    continue
                if line.startswith("#"):
                    continue
                lines.append(line.lower())
    except Exception:
        pass
    return lines


def recent_accounts():
    return _read_email_lines(ACCOUNTS_FILE)[-ACCOUNT_HISTORY:]


def _used_emails():
    used = set(_read_email_lines(ACCOUNTS_FILE))
    used.update(_read_email_lines(ASSETS_FILE))
    return used


def record_account_email(email):
    email = (email or "").strip().lower()
    if not email:
        return
    with FILE_LOCK:
        lines = _read_email_lines(ACCOUNTS_FILE)
        lines.append(email)
        lines = lines[-ACCOUNT_HISTORY:]
        try:
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass


def dotted_sequence(username):
    gaps = len(username) - 1
    for dots in range(0, gaps + 1):
        for positions in itertools.combinations(range(gaps), dots):
            out = []
            for i, ch in enumerate(username):
                out.append(ch)
                if i in positions:
                    out.append(".")
            yield "".join(out)


def _next_variant(username, domain, used, after=None):
    reached = after is None
    for local in dotted_sequence(username):
        email = f"{local}@{domain}".lower()
        if not reached:
            if email == after:
                reached = True
            continue
        if email not in used:
            return email, reached
    return None, reached


def _next_numbered(username, domain, used, start=100):
    n = max(100, start)
    while n < 10000 and f"{username}{n}@{domain}" in used:
        n += 1
    return f"{username}{n}@{domain}"


def next_account_email(base_email):
    try:
        username, domain = base_email.strip().split("@", 1)
    except ValueError:
        raise Exception("Invalid email format provided.")

    username = username.lower()
    domain = domain.lower()
    recent = recent_accounts()
    used = _used_emails()
    last = recent[-1] if recent else None

    if last and last.endswith("@" + domain):
        match = re.match(rf"^{re.escape(username)}(\d+)$", last.split("@", 1)[0])
        if match:
            return _next_numbered(username, domain, used, int(match.group(1)) + 1)

    email, saw_last = _next_variant(username, domain, used, after=last)
    if email is None and last is not None and not saw_last:
        email, _ = _next_variant(username, domain, used)
    if email is None:
        email = _next_numbered(username, domain, used)
    return email


def claim_account_email(base_email):
    """Picks the next dotted variant and records it in one atomic step.

    Two instances starting together would otherwise both read the same
    accounts.txt and pick the same address.
    """
    with FILE_LOCK:
        email = next_account_email(base_email)
        record_account_email(email)
        return email


def generate_password(length=18):
    rng = random.SystemRandom()
    lower = "abcdefghijkmnopqrstuvwxyz"
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    digits = "23456789"
    symbols = "!@#$%^&*"
    pools = (lower, upper, digits, symbols)
    chars = [rng.choice(pool) for pool in pools]
    alphabet = "".join(pools)
    chars.extend(rng.choice(alphabet) for _ in range(max(0, length - len(chars))))
    rng.shuffle(chars)
    return "".join(chars)


def save_api_key(key):
    key = (key or "").strip()
    name = os.path.basename(KEYS_FILE)
    if not key:
        return False
    with FILE_LOCK:
        try:
            if os.path.exists(KEYS_FILE):
                with open(KEYS_FILE, "r", encoding="utf-8", errors="replace") as f:
                    if key in {line.strip() for line in f}:
                        return False
            with open(KEYS_FILE, "a", encoding="utf-8") as f:
                f.write(key + "\n")
        except Exception:
            return False
    ui.ok(f"API key saved to {name}.")
    return True


def ensure_output_files():
    for path in (KEYS_FILE, ACCOUNTS_FILE):
        if os.path.exists(path):
            continue
        try:
            with open(path, "a", encoding="utf-8"):
                pass
        except Exception:
            pass


def _remember_base_email(email):
    try:
        with open(ASSETS_FILE, "a", encoding="utf-8") as f:
            f.write((email or "").strip().lower() + "\n")
    except Exception:
        pass


def get_base_regular_email():
    email_to_use = ""
    name = os.path.basename(ASSETS_FILE)
    if os.path.exists(ASSETS_FILE):
        lines = _read_email_lines(ASSETS_FILE)
        if lines:
            email_to_use = lines[0]

    if not email_to_use:
        email_to_use = ui.ask("Enter your regular home email (e.g. user@gmail.com):")
        if "@" not in email_to_use:
            raise Exception("That doesn't look like an email address.")
        _remember_base_email(email_to_use)
    return email_to_use


# --- hCaptcha solving (NoneCap API) ---
CAPTCHA_API_URL = "https://api.nonecap.com/v1/solves"
CAPTCHA_TIMEOUT = 180
CAPTCHA_POLL_WAIT = 90
CAPTCHA_WAIT = 12
CAPTCHA_CLICK_TIMEOUT = 1500

CAPTCHA_FRAME_SELECTOR = (
    'iframe[src*="frame=checkbox"], iframe[src*="hcaptcha.com"][title*="checkbox" i]'
)

CAPTCHA_INFO_JS = r"""() => {
    const FRAME_SEL = __FRAME_SEL__;
    const roots = [];
    const seenRoots = new Set();
    const collect = (root) => {
        if (!root || seenRoots.has(root)) return;
        seenRoots.add(root);
        roots.push(root);
        let nodes = [];
        try { nodes = [...root.querySelectorAll('*')]; } catch (e) { return; }
        for (const el of nodes) if (el.shadowRoot) collect(el.shadowRoot);
    };
    collect(document);

    const all = [];
    const seenEls = new Set();
    for (const root of roots) {
        for (const el of root.querySelectorAll('*')) {
            if (!seenEls.has(el)) { seenEls.add(el); all.push(el); }
        }
    }

    const frames = all.filter((el) => el.tagName === 'IFRAME' && /hcaptcha\.com/i.test(el.src || ''));
    const clickFrames = all.filter((el) => el.tagName === 'IFRAME' && el.matches(FRAME_SEL));
    const widgets = all.filter((el) => el.matches('[data-sitekey], .h-captcha, [data-hcaptcha-widget-id]'));
    const areas = all.filter((el) => el.matches(
        'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
    ));
    const rq = all.find((el) => el.hasAttribute && el.hasAttribute('data-rqdata'));

    const param = (url) => {
        try {
            const parsed = new URL(url, location.href);
            const query = parsed.searchParams.get('sitekey');
            if (query) return query;
            const hash = parsed.hash ? parsed.hash.slice(1) : '';
            return new URLSearchParams(hash).get('sitekey');
        } catch (e) {
            return null;
        }
    };

    let sitekey = null;
    for (const el of widgets) {
        const key = el.getAttribute('data-sitekey');
        if (key) { sitekey = key; break; }
    }
    if (!sitekey) {
        for (const el of clickFrames.concat(frames)) {
            const key = param(el.src);
            if (key) { sitekey = key; break; }
        }
    }
    if (!sitekey) {
        try {
            for (const entry of performance.getEntriesByType('resource')) {
                if (!/hcaptcha\.com/i.test(entry.name)) continue;
                const key = param(entry.name);
                if (key) { sitekey = key; break; }
            }
        } catch (e) {}
    }
    if (!sitekey && window.__fccCaptchaSitekey) sitekey = window.__fccCaptchaSitekey;

    const area = areas[0] || null;
    const value = area ? (area.value || '').trim() : '';
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
    };

    return {
        present: !!(widgets.length || frames.length || area),
        frames: frames.length,
        clickable: clickFrames.some(visible),
        widgets: widgets.length,
        sitekey: sitekey || null,
        rqdata: rq ? rq.getAttribute('data-rqdata') : null,
        solved: value.length > 20,
        ours: !!(value && window.__fccCaptchaToken && value === window.__fccCaptchaToken),
        hooks: (window.__fccCaptchaCallbacks || []).length,
        callbacks: all
            .filter((el) => el.getAttribute && el.getAttribute('data-callback'))
            .map((el) => el.getAttribute('data-callback'))
            .filter((name) => name && typeof window[name] === 'function'),
    };
}"""

CAPTCHA_INJECT_JS = r"""(token) => {
    const roots = [document];
    const collect = (root) => {
        for (const el of root.querySelectorAll('*')) {
            if (el.shadowRoot) { roots.push(el.shadowRoot); collect(el.shadowRoot); }
        }
    };
    try { collect(document); } catch (e) {}

    const write = (el) => {
        el.value = token;
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
    };

    const firstArea = document.querySelector(
        'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
    );
    let widgetId = null;
    if (firstArea && firstArea.id) {
        const match = firstArea.id.match(/-([^-]+)$/);
        if (match) widgetId = match[1];
    }

    let patched = 0;
    const hc = window.hcaptcha;
    if (hc && typeof hc.getResponse === 'function') {
        if (!hc.__fccOriginalGetResponse) {
            hc.__fccOriginalGetResponse = hc.getResponse;
            hc.__fccOriginalGetRespKey = hc.getRespKey;
        }
        const plain = hc.__fccOriginalGetResponse;
        const key = hc.__fccOriginalGetRespKey;
        hc.getResponse = function (id) {
            return window.__fccCaptchaToken || (plain ? plain.call(hc, id) : '');
        };
        hc.getRespKey = function (id) {
            return window.__fccCaptchaRespKey || (key ? key.call(hc, id) : '');
        };
        patched = 1;
    }

    window.__fccCaptchaToken = token;

    let written = 0;
    for (const root of roots) {
        for (const el of root.querySelectorAll(
            'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
        )) {
            write(el);
            written += 1;
        }
    }
    if (!written) {
        const area = document.createElement('textarea');
        area.name = 'h-captcha-response';
        area.style.display = 'none';
        document.body.appendChild(area);
        write(area);
        written = 1;
    }

    const fired = [];
    const seenNames = new Set();
    for (const root of roots) {
        for (const el of root.querySelectorAll('[data-callback]')) {
            const name = el.getAttribute('data-callback');
            if (!name || seenNames.has(name) || typeof window[name] !== 'function') continue;
            seenNames.add(name);
            try { window[name](token); fired.push(name); } catch (e) {}
        }
    }

    const NAMES = /^(callback|onSuccess|onVerify|onToken|onCaptcha.*|onComplete|onSolve|handleVerify|verifyCallback|captchaCallback)$/i;
    const seenFns = new Set();
    const callProps = (props, tag) => {
        if (!props || typeof props !== 'object') return;
        for (const key of Object.keys(props)) {
            if (!NAMES.test(key)) continue;
            const fn = props[key];
            if (typeof fn !== 'function' || seenFns.has(fn)) continue;
            seenFns.add(fn);
            try { fn(token); fired.push(tag + ':' + key); } catch (e) {}
        }
    };
    const fiberKeyOf = (node) => Object.keys(node).find((k) => k.startsWith('__reactFiber$'));
    const propsKeyOf = (node) => Object.keys(node).find((k) => k.startsWith('__reactProps$'));

    const seeds = [];
    for (const root of roots) {
        for (const el of root.querySelectorAll(
            'iframe, [data-sitekey], .h-captcha, textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
        )) {
            seeds.push(el);
        }
    }
    for (const el of seeds) {
        const propsKey = propsKeyOf(el);
        if (propsKey) callProps(el[propsKey], 'props');
        const fiberKey = fiberKeyOf(el);
        let fiber = fiberKey ? el[fiberKey] : null;
        let depth = 0;
        while (fiber && depth < 30) {
            callProps(fiber.memoizedProps, 'fiber');
            callProps(fiber.pendingProps, 'fiber');
            fiber = fiber.return;
            depth += 1;
        }
    }

    window.__fccCaptchaToken = token;

    let native = 0;
    for (const cb of (window.__fccCaptchaCallbacks || [])) {
        if (typeof cb !== 'function') continue;
        try { cb(token); native += 1; } catch (e) {}
    }

    return { written, callbacks: fired, native, widget: widgetId, patched };
}"""

CAPTCHA_CLEAR_JS = r"""() => {
    const mine = window.__fccCaptchaToken || '';
    let cleared = 0;
    document.querySelectorAll(
        'textarea[name="h-captcha-response"], textarea[name="g-recaptcha-response"]'
    ).forEach((el) => {
        if (mine && (el.value || '') === mine) {
            el.value = '';
            el.dispatchEvent(new Event('input', { bubbles: true }));
            cleared += 1;
        }
    });
    window.__fccCaptchaToken = '';
    return cleared;
}"""

CAPTCHA_HOOK_JS = r"""(() => {
    if (window.__fccCaptchaHooked) return;
    window.__fccCaptchaHooked = true;

    const list = window.__fccCaptchaCallbacks = window.__fccCaptchaCallbacks || [];

    const keep = (options) => {
        try {
            if (options && options.sitekey && !window.__fccCaptchaSitekey) {
                window.__fccCaptchaSitekey = options.sitekey;
            }
            if (options && typeof options.callback === 'function' && !list.includes(options.callback)) {
                list.push(options.callback);
            }
        } catch (e) {}
    };

    const patch = () => {
        const h = window.hcaptcha;
        if (!h || typeof h.render !== 'function' || h.render.__fccWrapped) return false;
        const render = h.render;
        const wrapped = function (element, options) {
            keep(options);
            return render.apply(this, arguments);
        };
        wrapped.__fccWrapped = true;
        h.render = wrapped;
        return true;
    };

    if (!patch()) {
        const timer = setInterval(() => {
            if (patch()) clearInterval(timer);
        }, 10);
        setTimeout(() => clearInterval(timer), 30000);
    }
})();"""


def install_captcha_hook(context):
    try:
        context.add_init_script(CAPTCHA_HOOK_JS)
    except Exception as e:
        ui.detail(f"Captcha callback hook not installed ({e.__class__.__name__}).")


def _nonecap_key():
    key = ""
    try:
        if not os.path.exists(ASSETS_FILE):
            return ""
        with open(ASSETS_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                match = re.match(r"\s*(?:nonecap|nonecapapi)\s*=\s*(\S+)", line, re.IGNORECASE)
                if match:
                    key = match.group(1).strip().strip('"').strip("'")
    except Exception:
        return ""
    return key


def _nonecap_call(method, path, api_key, payload=None, timeout=60):
    headers = {
        "accept": "application/json",
        "authorization": f"Bearer {api_key}",
    }
    if payload is not None:
        headers["content-type"] = "application/json"
    request = urllib.request.Request(
        CAPTCHA_API_URL + path,
        data=None if payload is None else json.dumps(payload).encode("utf-8"),
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        code, message = "", body
        try:
            err = (json.loads(body or "{}") or {}).get("error") or {}
            code = err.get("code") or ""
            message = err.get("message") or err.get("param") or body
        except Exception:
            pass
        detail = f"{code}: {message}".strip(": ") or "request failed"
        return None, e.code, detail[:160]
    except Exception as e:
        return None, 0, f"{e.__class__.__name__}: {str(e)[:160]}"

    try:
        data = json.loads(body or "{}")
    except Exception:
        return None, status, f"unreadable response: {body[:120]}"
    return (data if isinstance(data, dict) else {}), status, None


def _nonecap_solve(api_key, sitekey, page_url, rqdata=None, timeout=CAPTCHA_TIMEOUT):
    payload = {
        "type": "hcaptcha_enterprise" if rqdata else "hcaptcha",
        "sitekey": sitekey,
        "url": page_url,
    }
    if rqdata:
        payload["rqdata"] = rqdata

    deadline = time.time() + timeout
    solve, status, error = _nonecap_call(
        "POST", f"?wait={CAPTCHA_POLL_WAIT}", api_key, payload,
        timeout=CAPTCHA_POLL_WAIT + 30,
    )

    while True:
        if error:
            return None, (f"HTTP {status} — {error}" if status else error), {}

        state = (solve.get("status") or "").lower()
        token = (solve.get("token") or "").strip()

        if token and state in ("", "solved"):
            return token, None, {
                "id": solve.get("id") or "",
                "user_agent": solve.get("user_agent") or "",
                "resp_key": solve.get("resp_key") or "",
                "credits": solve.get("credits_charged"),
            }

        if state in ("failed", "cancelled", "expired"):
            reason = solve.get("error")
            if isinstance(reason, dict):
                reason = reason.get("message") or reason.get("code")
            return None, f"solve {state}: {reason or 'no reason given'}", {}

        solve_id = solve.get("id") or ""
        if not solve_id or time.time() >= deadline:
            return None, "the solve did not finish in time", {}

        time.sleep(1)
        solve, status, error = _nonecap_call(
            "GET", f"/{solve_id}?wait={CAPTCHA_POLL_WAIT}", api_key,
            timeout=CAPTCHA_POLL_WAIT + 30,
        )


def _nonecap_solve_with_ui(api_key, sitekey, page_url, rqdata=None):
    box = {}

    def work():
        try:
            box["result"] = _nonecap_solve(api_key, sitekey, page_url, rqdata)
        except Exception as e:
            box["result"] = (None, f"{e.__class__.__name__}: {e}", {})

    thread = threading.Thread(target=work, daemon=True)
    started = time.time()
    thread.start()
    while thread.is_alive():
        ui.set_note(f"NoneCap solving {int(time.time() - started)}s")
        time.sleep(0.25)
    thread.join()
    ui.set_note("")
    return box.get("result", (None, "solver thread failed", {}))


CAPTCHA_UA_JS = r"""(ua) => {
    try {
        Object.defineProperty(navigator, 'userAgent', { get: () => ua, configurable: true });
        Object.defineProperty(navigator, 'appVersion', {
            get: () => ua.replace(/^Mozilla\//, ''), configurable: true,
        });
        return true;
    } catch (e) { return false; }
}"""


def _align_user_agent(page, ua):
    ua = (ua or "").strip()
    if not ua:
        return
    try:
        if page.evaluate("navigator.userAgent") == ua:
            return
    except Exception:
        return
    try:
        page.set_extra_http_headers({"User-Agent": ua})
    except Exception as e:
        ui.detail(f"Could not align the User-Agent ({e.__class__.__name__}).")
        return
    try:
        page.evaluate(CAPTCHA_UA_JS, ua)
    except Exception:
        pass
    ui.detail("User-Agent aligned with the solver's browser.")


def _captcha_info(page):
    js = CAPTCHA_INFO_JS.replace("__FRAME_SEL__", json.dumps(CAPTCHA_FRAME_SELECTOR))
    try:
        info = page.evaluate(js)
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def _wait_for_captcha(page, timeout=None):
    budget = CAPTCHA_WAIT if timeout is None else timeout
    started = time.time()
    told = False
    while True:
        info = _captcha_info(page)
        if info and info.get("present"):
            return info
        if time.time() - started >= budget:
            break
        if not told and time.time() - started > 1.5:
            ui.detail(f"Waiting for the hCaptcha widget to render (up to {budget}s)...")
            told = True
        time.sleep(0.4)
    ui.warn(f"No hCaptcha appeared on the page within {budget}s.")
    return None


def _clear_captcha_token(page):
    try:
        cleared = page.evaluate(CAPTCHA_CLEAR_JS)
    except Exception:
        return 0
    return cleared or 0


def _try_captcha_checkbox(page):
    for selector in ("#checkbox", '[role="checkbox"]', "text=I am human"):
        try:
            box = page.frame_locator(CAPTCHA_FRAME_SELECTOR).locator(selector).first
            box.wait_for(state="visible", timeout=CAPTCHA_CLICK_TIMEOUT)
            box.click(timeout=CAPTCHA_CLICK_TIMEOUT)
            return True
        except Exception:
            continue
    return False


def _click_solves(page):
    ui.detail("Clicking the hCaptcha checkbox...")
    if not _try_captcha_checkbox(page):
        return False
    for _ in range(8):
        page.wait_for_timeout(350)
        after = _captcha_info(page)
        if after and after.get("solved") and not after.get("ours"):
            ui.ok("hCaptcha solved by its checkbox.")
            return True
    ui.detail("The checkbox asked for a challenge — trying the NoneCap solver.")
    return False


def solve_hcaptcha(page, force=False, click_first=False):
    info = _wait_for_captcha(page)
    if not info:
        return "absent"

    if info.get("solved") and (not force or not info.get("ours")):
        ui.detail("hCaptcha already carries a response — not solving again.")
        return "already-solved"

    if click_first and info.get("clickable") and _click_solves(page):
        return "clicked"

    info = _captcha_info(page) or info
    sitekey = info.get("sitekey")
    token = None
    solver = {}

    api_key = _nonecap_key()
    if not sitekey:
        ui.warn("hCaptcha is on the page but no sitekey was readable — leaving it to you.")
    elif not api_key:
        ui.warn(f"No nonecap key in {os.path.basename(ASSETS_FILE)} — leaving the hCaptcha to you.")
    else:
        ui.info(f"hCaptcha detected (sitekey {sitekey}) — asking NoneCap to solve it...")
        token, error, solver = _nonecap_solve_with_ui(
            api_key, sitekey, page.url, info.get("rqdata")
        )
        if token:
            ui.ok(f"Captcha token received (key {mask_key(api_key)}).")
        else:
            ui.warn(f"NoneCap: {error}")
            token = None

    if not token:
        if not click_first and info.get("clickable") and _click_solves(page):
            return "clicked"
        return None

    _align_user_agent(page, solver.get("user_agent"))

    try:
        result = page.evaluate(CAPTCHA_INJECT_JS, token) or {}
    except Exception as e:
        ui.warn(f"Could not inject the captcha token ({e.__class__.__name__}).")
        return None

    fired = result.get("callbacks") or []
    native = result.get("native") or 0
    ui.ok(
        f"Token injected ({result.get('written', 0)} field(s), "
        f"{len(fired) + native} handler(s))."
    )
    if result.get("patched"):
        ui.detail("hCaptcha's getResponse() now answers with the solved token.")
    page.wait_for_timeout(400)
    after = _captcha_info(page)
    if after and not after.get("solved"):
        ui.detail("The page cleared the token field — relying on the form callback.")
    return token


def submit_email_and_wait_for_success(page, send_btn, base_email):
    start_time = time.time()
    error_logged = False

    while time.time() - start_time < 300:
        target_email = claim_account_email(base_email)
        ui.info(f"Trying email: {target_email}")

        email_input = page.locator('input[placeholder="Email"]')
        email_input.wait_for(state="visible", timeout=10000)
        email_input.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        email_input.fill(target_email)

        _clear_captcha_token(page)
        send_btn.scroll_into_view_if_needed()
        send_btn.click(force=True)

        attempt_start = time.time()
        captcha_done = False
        last_probe = 0.0
        while time.time() - attempt_start < 90:
            try:
                success_banner = page.locator('text=/Email verification code sent successfully/i').first
                if success_banner.is_visible():
                    ui.ok("Confirmation banner: verification code sent.")
                    return target_email

                error_banner = page.locator('text=/error|invalid|failed|solve correctly|limit/i').first
                if error_banner.is_visible():
                    if not error_logged:
                        ui.warn("Error notice detected — retrying with a new variation.")
                        error_logged = True
                    break

                if not captcha_done and time.time() - last_probe > 1.0:
                    last_probe = time.time()
                    info = _captcha_info(page)
                    if info and info.get("present") and not info.get("solved"):
                        ui.info("hCaptcha appeared — solving it...")
                        result = solve_hcaptcha(page, force=True)
                        captcha_done = True
                        if result in (None, "absent"):
                            ui.warn("Captcha not answered automatically — solve it in the browser.")
                        else:
                            ui.ok("Captcha answered; the code is being sent.")
                    elif time.time() - attempt_start > 4:
                        captcha_done = True
            except Exception:
                pass

            time.sleep(0.25)
        else:
            error_logged = False
            continue

    raise Exception("Timed out trying email variations after multiple error cycles.")


# --- Ace API key capture ---
ACE_COPY_JS = r"""() => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const isMasked = (s) => /\.{3,}|\u2026/.test(s || '');
    const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

    const keyish = (s) => {
        const t = norm(s).replace(/^Bearer\s+/i, '').trim();
        if (!t) return false;
        if (UUID.test(t) || /^[0-9a-f]{8}-[0-9a-f]{4}/i.test(t)) return false;
        if (!/^[A-Za-z0-9._\-]{2,}$/.test(t)) return false;
        return isMasked(t) || (t.length >= 18 && t.length <= 128);
    };

    let rect = null;

    for (const el of document.querySelectorAll('input, textarea')) {
        if (!keyish(el.value || '')) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 60 || r.height < 14) continue;
        rect = r;
        break;
    }

    if (!rect) {
        let best = null, bestArea = Infinity;
        for (const el of document.querySelectorAll('div, span, p, a, select')) {
            if (!keyish(norm(el.textContent))) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 60 || r.height < 14 || r.height > 60) continue;
            const a = r.width * r.height;
            if (a < bestArea) { bestArea = a; best = r; }
        }
        rect = best;
    }

    if (!rect) return { rect: null, candidates: [] };

    const cy = rect.y + rect.height / 2;
    const seen = new Set();
    const candidates = [];

    const add = (el, how) => {
        if (el.closest('[class*="arrow" i], [class*="down" i], [class*="chevron" i]')) return;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.width > 90 || r.height < 4 || r.height > 90) return;
        const sy = r.y + r.height / 2;
        const sx = r.x + r.width / 2;
        if (Math.abs(sy - cy) > 28) return;
        if (sx <= rect.right - 30) return;
        const target = el.closest('button, [role="button"], a') || el;
        if (seen.has(target)) return;
        seen.add(target);
        candidates.push({ el: target, x: sx, y: sy, how });
    };

    for (const el of document.querySelectorAll(
        '[aria-label*="copy" i], [title*="copy" i], .anticon-copy, [class*="copy" i], [data-icon="copy"]'
    )) add(el, 'attr');

    for (const svg of document.querySelectorAll('svg')) add(svg, 'geom');

    candidates.sort((a, b) => (a.how === b.how ? b.x - a.x : (a.how === 'attr' ? -1 : 1)));
    candidates.forEach((c, i) => {
        try { c.el.setAttribute('data-ace-copy-id', String(i)); } catch (e) {}
    });

    return {
        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height },
        candidates: candidates.map(c => ({ x: c.x, y: c.y, how: c.how }))
    };
}"""

ACE_CLICK_COPY_JS = r"""(i) => {
    const el = document.querySelector('[data-ace-copy-id="' + i + '"]');
    if (!el) return false;
    try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    try { el.click(); } catch (e) { return false; }
    return true;
}"""

ACE_READ_KEY_JS = r"""() => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const isMasked = (s) => /\.{3,}|\u2026/.test(s || '');
    for (const el of document.querySelectorAll('input, textarea')) {
        let v = norm(el.value || '').replace(/^Bearer\s+/i, '').trim();
        if (!v || isMasked(v)) continue;
        if (!/^[A-Za-z0-9._\-]{16,128}$/.test(v)) continue;
        return v;
    }
    return '';
}"""

APPID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

def read_clipboard(page):
    try:
        return (page.evaluate("() => navigator.clipboard.readText()") or "").strip()
    except Exception:
        return ""

def is_masked(token):
    return "..." in token or "\u2026" in token

def _wait_for_clipboard_change(page, marker, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cur = read_clipboard(page)
        if cur and cur != marker:
            return re.sub(r"^Bearer\s+", "", cur, flags=re.IGNORECASE).strip()
        page.wait_for_timeout(150)
    return ""

def _usable_key(cand):
    return bool(cand) and not is_masked(cand) and not APPID_RE.match(cand) and len(cand) >= 16

def grab_ace_api_key(ace_page):
    ace_page.bring_to_front()
    ace_page.wait_for_timeout(800)

    key = ""
    for attempt in range(1, 4):
        try:
            direct = (ace_page.evaluate(ACE_READ_KEY_JS) or "").strip()
        except Exception:
            direct = ""
        if _usable_key(direct):
            ui.ok("Full API key is already visible — reading it directly.")
            key = direct
            break

        marker = f"__ace_clip_cleared_{attempt}__"
        try:
            ace_page.evaluate("(m) => navigator.clipboard.writeText(m)", marker)
        except Exception:
            marker = read_clipboard(ace_page)

        info = None
        try:
            info = ace_page.evaluate(ACE_COPY_JS)
        except Exception as e:
            ui.err(f"Copy glyph lookup failed: {e}")

        cands = (info or {}).get("candidates") or []

        for i, c in enumerate(cands):
            try:
                clicked = bool(ace_page.evaluate(ACE_CLICK_COPY_JS, i))
            except Exception:
                clicked = False

            cand = _wait_for_clipboard_change(ace_page, marker, 2.5) if clicked else ""

            if not _usable_key(cand):
                try:
                    ace_page.mouse.click(c["x"], c["y"])
                except Exception:
                    pass
                mouse_cand = _wait_for_clipboard_change(ace_page, marker, 2.0)
                if _usable_key(mouse_cand):
                    cand = mouse_cand

            if not _usable_key(cand):
                try:
                    revealed = (ace_page.evaluate(ACE_READ_KEY_JS) or "").strip()
                except Exception:
                    revealed = ""
                if _usable_key(revealed):
                    cand = revealed

            if _usable_key(cand):
                ui.ok(f"Copy glyph #{i + 1} produced the full API key.")
                key = cand
                break

        if key:
            break
        ace_page.wait_for_timeout(600)

    if not key:
        ui.err("Could not read a full key from any glyph near the API key field.")
        while not key:
            manual = ui.ask("Paste the full Ace Data Cloud API key:")
            manual = re.sub(r"^Bearer\s+", "", manual, flags=re.IGNORECASE).strip()
            if not manual or is_masked(manual) or len(manual) < 16:
                ui.warn("Still masked or too short — paste the full key.")
            elif APPID_RE.match(manual):
                ui.warn("That's an App ID, not the API key.")
            else:
                key = manual

    ui.ok(f"API key captured ({len(key)} chars).")
    _store_on_clipboard(ace_page, key)
    ui.key(key, "API KEY", "Full key printed above and copied to the clipboard.")
    return key


def _store_on_clipboard(page, key):
    try:
        page.evaluate(
            """async (v) => {
                try { await navigator.clipboard.writeText(v); return true; }
                catch (e) { return false; }
            }""",
            key,
        )
    except Exception:
        pass


EARN_CREDIT_PATTERNS = (
    re.compile(r"earn\s*\d*\s*credits?", re.IGNORECASE),
    re.compile(r"earn.{0,12}free|free.{0,12}credit", re.IGNORECASE),
    re.compile(r"claim.{0,15}credit|receive.{0,15}credit|acquire", re.IGNORECASE),
)
EARN_APPLY_RE = re.compile(r"^\s*Apply\s*$", re.IGNORECASE)


def _first_visible(locator, limit=25):
    try:
        count = locator.count()
    except Exception:
        return None
    for i in range(min(count, limit)):
        el = locator.nth(i)
        try:
            if el.is_visible():
                return el
        except Exception:
            continue
    return None


def _find_credit_control(page):
    scopes = ("button:visible, a:visible, [role='button']:visible", "span:visible")
    for pattern in EARN_CREDIT_PATTERNS:
        for scope in scopes:
            el = _first_visible(page.locator(scope).filter(has_text=pattern))
            if el is not None:
                return el
    return None


CREDIT_OK_RE = re.compile(r"success|claimed|credited|added to your account", re.IGNORECASE)


def _credit_confirmed(page, timeout=0):
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            text = page.evaluate("() => document.body.innerText || ''")
        except Exception:
            text = ""
        if CREDIT_OK_RE.search(text or ""):
            return True
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(300)


DEDICATED_TAG_JS = r"""() => {
    const re = /dedicated\s*balan/i;
    const flat = (s) => (s || '').replace(/\s+/g, ' ').trim();
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        if (!el.getClientRects().length || r.width <= 0 || r.height <= 0) return false;
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
    };
    const clickable = (el) => {
        if (!el || el.nodeType !== 1) return false;
        if (el.matches('button,a,[role="button"],[role="tab"],[onclick]')) return true;
        return getComputedStyle(el).cursor === 'pointer';
    };
    const area = (el) => {
        const r = el.getBoundingClientRect();
        return r.width * r.height;
    };

    const all = [...document.querySelectorAll('button,a,[role="button"],[role="tab"],div,span,li,p,strong,b')];
    const hits = all.filter((el) => re.test(flat(el.innerText || el.textContent)) && visible(el));
    if (!hits.length) return null;

    const label = hits.sort((a, b) => area(a) - area(b))[0];
    let pick = label;
    if (!clickable(pick)) {
        let node = pick.parentElement;
        for (let i = 0; i < 6 && node; i++) {
            if (clickable(node)) { pick = node; break; }
            node = node.parentElement;
        }
    }

    document.querySelectorAll('[data-fcc-dedicated="1"]').forEach((el) => el.removeAttribute('data-fcc-dedicated'));
    pick.setAttribute('data-fcc-dedicated', '1');
    return flat(label.innerText || label.textContent).slice(0, 80);
}"""


def select_dedicated_balance(page, timeout=6.0):
    deadline = time.time() + max(0.0, timeout)
    label = None
    while True:
        try:
            label = page.evaluate(DEDICATED_TAG_JS)
        except Exception:
            label = None
        if label or time.time() >= deadline:
            break
        page.wait_for_timeout(300)

    if not label:
        return False

    try:
        target = page.locator('[data-fcc-dedicated="1"]').first
        target.scroll_into_view_if_needed()
        target.click(force=True)
    except Exception:
        return False

    page.wait_for_timeout(800)
    ui.ok(f"Opened '{label}'.")
    return True


def claim_free_credit(page, select_dedicated=True):
    ui.info(f"Opening {CREDIT_URL} ...")
    page.bring_to_front()
    try:
        page.goto(CREDIT_URL, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        ui.warn(f"Could not open the credit page ({e.__class__.__name__}).")
        return False

    page.wait_for_timeout(2500)
    for _ in range(2):
        page.mouse.wheel(0, 800)
        page.wait_for_timeout(400)

    earn = _find_credit_control(page)
    if earn is None:
        return False

    try:
        earn.scroll_into_view_if_needed()
        earn.click(force=True)
    except Exception:
        return False

    page.wait_for_timeout(1800)

    apply_btn = _first_visible(page.locator("button:visible").filter(has_text=EARN_APPLY_RE))
    if apply_btn is None:
        apply_btn = _first_visible(page.locator('text="Apply"'))
    if apply_btn is None:
        if _credit_confirmed(page):
            ui.ok("Free credit claimed.")
            if select_dedicated:
                select_dedicated_balance(page)
            return True
        return False

    try:
        apply_btn.scroll_into_view_if_needed()
        apply_btn.click(force=True)
    except Exception:
        return False

    if _credit_confirmed(page, 12):
        ui.ok("Free credit claimed.")

    if select_dedicated:
        select_dedicated_balance(page)
    return True


def fill_field(page, el, value, label, settle_ms=400):
    def current():
        try:
            return (el.input_value() or "").strip()
        except Exception:
            return ""

    el.scroll_into_view_if_needed()
    if current() == value:
        return

    try:
        el.fill(value, timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)
    if current() == value:
        return

    try:
        el.click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(value, delay=20)
    except Exception:
        pass


# --- free-claude-code admin panel ---
FCC_SAVE_RE = re.compile(r"^\s*Save\s*$", re.IGNORECASE)
FCC_CANCEL_RE = re.compile(r"^\s*Cancel\s*$", re.IGNORECASE)
FCC_APPLY_RE = re.compile(r"^\s*Apply\s*$", re.IGNORECASE)
FCC_ADD_KEY_RE = re.compile(r"^\s*Add key\s*$", re.IGNORECASE)
FCC_ADD_PROVIDER_RE = re.compile(r"^\s*Add provider\s*$", re.IGNORECASE)
FCC_EDIT_RE = re.compile(r"^\s*Edit\s*$", re.IGNORECASE)

FCC_FIND_CARD_JS = r"""(name) => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const want = name.toLowerCase();
    const leaves = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,span,div,strong,b')];
    let title = leaves.find((el) => norm(el.textContent) === want && el.children.length === 0);
    if (!title) {
        title = leaves.find((el) => {
            const t = norm(el.textContent);
            return t.includes(want) && t.length <= want.length + 12;
        });
    }
    if (!title) return null;
    let card = title;
    for (let i = 0; i < 10 && card; i++) {
        if ([...card.querySelectorAll('button')].some((b) => norm(b.textContent) === 'edit')) break;
        card = card.parentElement;
    }
    if (!card) return null;
    card.setAttribute('data-fcc-card', '1');
    return true;
}"""


def open_fcc_admin(context, page=None):
    ui.info(f"Opening {FCC_ADMIN_URL} ...")
    admin = page if page is not None else context.new_page()
    try:
        admin.goto(FCC_ADMIN_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        detail = (str(e).splitlines() or [""])[0][:140]
        raise Exception(f"Could not reach {FCC_ADMIN_URL} — {detail}")

    admin.wait_for_timeout(1200)
    try:
        admin.wait_for_selector("text=/custom providers/i", timeout=20000)
    except Exception:
        raise Exception(
            f"{FCC_ADMIN_URL} loaded but never showed a 'Custom providers' section — "
            "is that the free-claude-code admin page?"
        )
    ui.ok("Admin panel ready.")
    return admin


_FCC_UNAVAILABLE = False


def prompt_fcc_running(context):
    global _FCC_UNAVAILABLE
    if _FCC_UNAVAILABLE:
        return None
    admin = None
    while True:
        try:
            admin = open_fcc_admin(context, admin)
            return admin
        except Exception as e:
            ui.err(str(e))
            answer = ui.ask("Do you want to install free-claude-code? [y/N]")
            if answer.strip().lower() in ("y", "yes"):
                ui.info("Running free-claude-code installer...")
                try:
                    subprocess.run(["powershell", "-Command", FCC_INSTALL_CMD], shell=True)
                    ui.ok("Installation script finished.")
                except Exception as ex:
                    ui.err(f"Installation failed: {ex}")
            choice = ui.ask("Press Enter to retry once it is running (type 'skip' to carry on without it)")
            if choice.strip().lower() in ("skip", "s"):
                _FCC_UNAVAILABLE = True
                ui.warn("Continuing without the admin panel — the key will be printed so you can add it later.")
                return None
            ui.info("Retrying the admin panel...")


def reuse_or_open_admin(context, admin):
    if admin is None or admin.is_closed():
        return prompt_fcc_running(context)
    try:
        admin.bring_to_front()
        admin.reload(wait_until="domcontentloaded", timeout=30000)
        admin.wait_for_selector("text=/custom providers/i", timeout=15000)
        ui.ok("Admin panel tab refreshed.")
        return admin
    except Exception:
        return prompt_fcc_running(context)


def _label_field(scope, label):
    try:
        loc = scope.get_by_label(label, exact=True)
        if loc.count():
            return loc.first
    except Exception:
        pass
    quoted = '"' + label.replace('"', "'") + '"'
    return scope.locator(
        f"xpath=.//*[self::label or self::div or self::span][normalize-space()={quoted}]"
        f"/following::input[1]"
    ).first


FCC_EDITOR_TAG_JS = r"""() => {
    const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const visible = (el) => {
        if (!el.getClientRects().length) return false;
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
    };
    const isKeyField = (el) => {
        if (el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA') return false;
        const type = (el.getAttribute('type') || 'text').toLowerCase();
        if (['checkbox', 'radio', 'hidden', 'file', 'submit', 'button', 'range'].includes(type)) return false;
        if (!visible(el)) return false;
        const ph = norm(el.getAttribute('placeholder'));
        const aria = norm(el.getAttribute('aria-label'));
        const name = norm(el.getAttribute('name'));
        return ph.includes('key') || ph.includes('token') || aria.includes('key') || name.includes('key');
    };

    document.querySelectorAll('[data-fcc-editor]').forEach((el) => el.removeAttribute('data-fcc-editor'));
    const fields = [...document.querySelectorAll('input, textarea')].filter(isKeyField);
    if (!fields.length) return null;

    let node = fields[fields.length - 1];
    let scope = null;
    while (node && node !== document.body) {
        const buttons = [...node.querySelectorAll('button')].filter(visible);
        if (buttons.some((b) => norm(b.textContent) === 'save')) { scope = node; break; }
        node = node.parentElement;
    }
    if (!scope) {
        const modals = [...document.querySelectorAll('[role="dialog"], .modal, [aria-modal="true"]')].filter(visible);
        scope = modals.length ? modals[modals.length - 1] : null;
    }
    if (!scope) return null;
    scope.setAttribute('data-fcc-editor', '1');
    return true;
}"""


def _editor_scope(page):
    try:
        page.evaluate(FCC_EDITOR_TAG_JS)
    except Exception:
        pass
    tag = page.locator('[data-fcc-editor="1"]')
    try:
        if tag.count():
            return tag.first
    except Exception:
        pass
    for sel in ('[role="dialog"]:visible', '.modal:visible'):
        try:
            dlg = page.locator(sel)
            if dlg.count():
                return dlg.last
        except Exception:
            continue
    return page


def _editor_closed(page):
    try:
        tag = page.locator('[data-fcc-editor="1"]')
        if tag.count():
            return not tag.last.is_visible()
    except Exception:
        pass
    try:
        return page.locator("button:visible").filter(has_text=FCC_SAVE_RE).count() == 0
    except Exception:
        return False


def _wait_editor_closed(page, seconds):
    deadline = time.time() + max(0.0, seconds)
    while True:
        if _editor_closed(page):
            return True
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(200)


def _close_editor(page, seconds=6.0):
    try:
        cancel = _editor_button(page, FCC_CANCEL_RE)
        if not cancel.count():
            return False
        cancel.scroll_into_view_if_needed()
        cancel.click(force=True)
    except Exception:
        return False
    return _wait_editor_closed(page, seconds)


def _editor_button(page, pattern):
    try:
        scope = _editor_scope(page)
        btn = scope.locator("button:visible").filter(has_text=pattern)
        if btn.count():
            return btn.last
    except Exception:
        pass
    return page.locator("button:visible").filter(has_text=pattern).last


ROW_REMOVE_JS = r"""(node) => {
    let parent = node.parentElement;
    for (let depth = 0; depth < 4 && parent; depth++) {
        if (parent.querySelectorAll('input, textarea').length > 1) return false;
        const hasRemove = [...parent.querySelectorAll('button')].some((b) => {
            const label = ((b.textContent || '') + ' ' + (b.getAttribute('aria-label') || '')
                + ' ' + (b.getAttribute('title') || '')).trim().toLowerCase();
            return label.includes('remove') || label.includes('delete');
        });
        if (hasRemove) return true;
        parent = parent.parentElement;
    }
    return false;
}"""


def _row_has_remove_button(el):
    try:
        return bool(el.evaluate(ROW_REMOVE_JS))
    except Exception:
        return False


def _key_rows(scope):
    rows = []
    try:
        inputs = scope.locator("input:visible, textarea:visible")
        total = inputs.count()
    except Exception:
        return rows

    for i in range(total):
        el = inputs.nth(i)
        try:
            ph = (el.get_attribute("placeholder") or "").lower()
            typ = (el.get_attribute("type") or "text").lower()
            aria = (el.get_attribute("aria-label") or "").lower()
            name = (el.get_attribute("name") or "").lower()
        except Exception:
            continue
        if typ in ("checkbox", "radio", "hidden", "file", "submit", "button", "range"):
            continue
        if "key" in ph or "token" in ph or "key" in aria or "key" in name:
            rows.append(el)
        elif _row_has_remove_button(el):
            rows.append(el)
    return rows


def _row_text(el):
    try:
        return (el.get_attribute("placeholder") or "").lower()
    except Exception:
        return ""


def _row_is_blank(el):
    try:
        return not (el.input_value() or "").strip()
    except Exception:
        return False


def _row_is_configured(el):
    return "configured" in _row_text(el) or "replace" in _row_text(el)


def _click_add_key(page, scope):
    before = len(_key_rows(scope))
    add = scope.locator("button:visible").filter(has_text=FCC_ADD_KEY_RE)
    if not add.count():
        add = page.locator("button:visible").filter(has_text=FCC_ADD_KEY_RE)
    if not add.count():
        return None

    btn = add.first
    btn.scroll_into_view_if_needed()
    try:
        btn.click(force=True)
    except Exception:
        pass

    deadline = time.time() + 5
    while time.time() < deadline:
        rows = _key_rows(scope)
        fresh = [r for r in rows if not _row_is_stored(r)]
        if len(rows) > before and fresh:
            return fresh[-1]
        page.wait_for_timeout(250)
    return None


def _row_is_stored(el):
    if _row_is_configured(el):
        return True
    return not _row_is_blank(el)


def _stored_key_count(rows):
    return sum(1 for r in rows if _row_is_stored(r))


def _type_key_into(page, target, ace_key):
    if _row_is_stored(target):
        raise Exception("Refusing to type into a row that already holds a stored key.")
    fill_field(page, target, ace_key, "API key")
    page.wait_for_timeout(500)
    try:
        held = (target.input_value() or "").strip()
    except Exception:
        held = ""
    if held != ace_key:
        raise Exception("The key field reset itself after typing.")


def _set_provider_key(page, ace_key):
    scope = _editor_scope(page)
    rows = _key_rows(scope)
    before = _stored_key_count(rows)

    blank = [r for r in rows if not _row_is_stored(r)]
    target = blank[-1] if blank else None
    if target is not None:
        pass
    else:
        target = _click_add_key(page, scope)

    if target is not None:
        try:
            _type_key_into(page, target, ace_key)
            return before
        except Exception:
            pass

    added = _click_add_key(page, scope)
    if added is None:
        raise Exception("The key would not stay in any key field.")

    _type_key_into(page, added, ace_key)
    return before


def _open_provider_editor(page):
    if page.evaluate(FCC_FIND_CARD_JS, PROVIDER_NAME):
        card = page.locator('[data-fcc-card="1"]').first
        card.scroll_into_view_if_needed()
        edit = card.locator("button:visible").filter(has_text=FCC_EDIT_RE).first
        if not edit.count():
            edit = card.locator("button").filter(has_text=FCC_EDIT_RE).first
        edit.click(force=True)
        return False

    add = page.locator("button:visible").filter(has_text=FCC_ADD_PROVIDER_RE).first
    if not add.count():
        add = page.locator("button").filter(has_text=FCC_ADD_PROVIDER_RE).first
    add.wait_for(state="visible", timeout=10000)
    add.scroll_into_view_if_needed()
    add.click(force=True)
    return True


def configure_ace_provider(page, ace_key):
    is_new = _open_provider_editor(page)
    try:
        page.locator("button:visible").filter(has_text=FCC_SAVE_RE).first.wait_for(
            state="visible", timeout=15000
        )
    except Exception as e:
        raise Exception(f"The provider editor did not open: {e}")

    scope = _editor_scope(page)
    if is_new:
        fill_field(page, _label_field(scope, "Name"), PROVIDER_NAME, "Provider name")
        fill_field(page, _label_field(scope, "Base URL"), PROVIDER_BASE_URL, "Base URL")

    ui.step("Enter API key")
    before = _set_provider_key(page, ace_key)

    ui.step("Save provider")
    save = _editor_button(page, FCC_SAVE_RE)
    save.scroll_into_view_if_needed()
    save.click(force=True)

    closed = _wait_editor_closed(page, 3.0)
    if not closed:
        _close_editor(page, 6.0)

    return True, before, None


def apply_pending_changes(page):
    apply_btn = page.locator("button:visible").filter(has_text=FCC_APPLY_RE).last
    if not apply_btn.count():
        return False

    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            if apply_btn.is_enabled():
                break
        except Exception:
            break
        page.wait_for_timeout(200)

    try:
        if not apply_btn.is_enabled():
            return False
    except Exception:
        return False

    apply_btn.scroll_into_view_if_needed()
    apply_btn.click(force=True)
    page.wait_for_timeout(1800)
    return True


def _registration_form_ready(page, timeout=0):
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            if page.locator('input[type="password"]:visible').count():
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(250)


REGISTER_TAB_TAG_JS = r"""() => {
    const flat = (s) => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        if (!el.getClientRects().length || r.width <= 0 || r.height <= 0) return false;
        const st = getComputedStyle(el);
        return st.display !== 'none' && st.visibility !== 'hidden' && st.opacity !== '0';
    };
    const area = (el) => {
        const r = el.getBoundingClientRect();
        return r.width * r.height;
    };
    const rank = (el) => (el.matches('[role="tab"],button,a,[role="button"]') ? 0 : 1);

    const hits = [...document.querySelectorAll('[role="tab"],button,a,[role="button"],div,span,li,p,strong,b')]
        .filter((el) => flat(el.innerText || el.textContent) === 'register' && visible(el));
    if (!hits.length) return null;

    hits.sort((a, b) => rank(a) - rank(b) || area(a) - area(b));
    const pick = hits[0];
    document.querySelectorAll('[data-fcc-register="1"]').forEach((el) => el.removeAttribute('data-fcc-register'));
    pick.setAttribute('data-fcc-register', '1');
    return flat(pick.innerText || pick.textContent);
}"""


def _select_register_tab(page):
    try:
        if page.evaluate(REGISTER_TAB_TAG_JS):
            tab = page.locator('[data-fcc-register="1"]').first
            tab.scroll_into_view_if_needed()
            tab.click(force=True)
            page.wait_for_timeout(1200)
            return True
    except Exception:
        pass
    try:
        page.locator('text="Register"').last.click(timeout=3000)
        page.wait_for_timeout(1200)
        return True
    except Exception:
        return False


def _goto(page, url, timeout=45000, attempts=2):
    last = None
    for _ in range(max(1, attempts)):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as e:
            last = e
            try:
                page.wait_for_timeout(800)
            except Exception:
                pass
    return False


def _docs_register_route(page):
    page.wait_for_timeout(1500)
    page.locator('text=/Receive.*Credit|Acquire/i').first.click(timeout=15000)
    page.locator('text=Apply').first.click(timeout=15000)
    page.wait_for_selector('input[type="password"]', timeout=15000)
    _select_register_tab(page)
    return True


def open_registration(page):
    ui.info(f"Opening {REGISTER_URL} ...")
    if _goto(page, REGISTER_URL):
        page.wait_for_timeout(1500)
        if _registration_form_ready(page, 3):
            ui.ok("Registration form ready.")
            return True
        _select_register_tab(page)
        if _registration_form_ready(page, 4):
            ui.ok("Registration form ready.")
            return True

    _goto(page, TARGET_URL)
    _docs_register_route(page)
    ui.ok("Registration form ready.")
    return True


def _bootstrap():
    """Runs once, before any browser opens."""
    ui.setup_begin([
        ("Python packages", "checking"),
        ("Chromium browser", "checking"),
        ("chromedriver", "checking"),
        ("Configuration", "reading your settings"),
        ("Gmail bridge", f"port {BRIDGE_PORT}"),
    ])
    for index in range(ui.count):
        ui.queue_note(index, "waiting for setup")
    ensure_environment()

    ui.setup_item("Configuration", "busy", "reading assets.txt")
    base_email = get_base_regular_email()
    ensure_output_files()
    ui.setup_item("Configuration", "ok", base_email)

    try:
        start_local_server(BRIDGE_PORT)
        ui.setup_item("Gmail bridge", "ok", f"http://localhost:{BRIDGE_PORT}")
    except OSError:
        ui.setup_item("Gmail bridge", "ok", f"http://localhost:{BRIDGE_PORT} (already up)")

    if sync_playwright is None:
        raise Exception("Playwright is unavailable — run: pip install playwright")
    ui.setup_end()
    return base_email


def _flow(page, context, index, base_email):
    """One full Ace -> free-claude-code run, on this instance's pages."""
    admin = None

    ui.step("Check admin panel")
    with FCC_LOCK:
        admin = prompt_fcc_running(context)
        if admin is not None:
            ui.ok(f"free-claude-code is up at {FCC_ADMIN_URL}")
            page.bring_to_front()

    ui.step("Open registration page")
    page.bring_to_front()
    open_registration(page)

    ui.step("Request verification code")
    send_btn = page.locator('input[placeholder*="Verification"] + button, div:has-text("Email Verification Code") button').first
    if not send_btn.count():
        send_btn = page.locator('button').filter(has_text=re.compile("Send Email Code|s$", re.IGNORECASE)).first
    send_btn.wait_for(state="visible", timeout=10000)
    used_email = submit_email_and_wait_for_success(page, send_btn, base_email)
    ui.worker_email(index, used_email)
    ui.ok(f"Verification code sent to {used_email}")

    ui.step("Receive verification code")
    ui.info("Waiting for local Gmail bridge...")
    code = wait_for_bridge_code(90)
    if not code:
        ui.warn("Bridge timed out waiting for the Gmail verification code.")
        code = ui.ask("Enter the verification code manually from your inbox:")
        if not code:
            raise Exception("Timed out and no manual verification code provided.")
    ui.ok("Verification code accepted.")

    ui.step("Fill registration form")
    password = generate_password()
    page.bring_to_front()
    page.fill('input[placeholder*="Verification"]', code)
    page.fill('input[placeholder="Password"]', password)
    ui.ok("Email code and password filled in.")

    ui.step("Submit registration")
    if (_captcha_info(page) or {}).get("present"):
        solve_hcaptcha(page, force=True)
    ui.info("Submitting registration...")
    reg_submit = page.locator('form button, .ant-form button, button[type="submit"]').filter(has_text=re.compile("^Register$", re.IGNORECASE)).last
    if not reg_submit.count():
        reg_submit = page.locator('.auth-modal button, .card button, div[role="dialog"] button').filter(has_text=re.compile("^Register$", re.IGNORECASE)).last
    if reg_submit.count():
        try:
            with page.expect_navigation(timeout=15000):
                reg_submit.click(force=True)
        except Exception:
            reg_submit.click(force=True)
    else:
        page.click('button:has-text("Register")')
    ui.ok("Registration submitted.")

    ui.step("Open console")
    page.wait_for_selector("text=Console", timeout=15000)
    page.click("text=Console")
    discard_late_codes()
    ui.ok("Console open.")

    ui.step("Open API key dialog")
    try:
        page.wait_for_selector("text=Create API Key", timeout=10000)
        page.click("text=Create API Key")
        ui.ok("API key dialog opened.")
    except Exception:
        ui.warn("No 'Create API Key' control — using existing console key.")
    page.wait_for_timeout(2500)

    ui.step("Capture API key")
    ace_key = grab_ace_api_key(page)

    ui.step("Save key to keys.txt")
    save_api_key(ace_key)

    # Every instance edits the same provider list, so this part is serialised.
    with FCC_LOCK:
        ui.step("Open provider settings")
        admin = reuse_or_open_admin(context, admin)
        if admin is None:
            raise Exception("free-claude-code is not running.")

        configure_ace_provider(admin, ace_key)

        ui.step("Apply changes")
        apply_pending_changes(admin)

    ui.step("Claim free credit")
    claim_free_credit(page, select_dedicated=False)

    ui.step("Open Dedicated Balance")
    select_dedicated_balance(page)
    return ace_key


def _show_traceback():
    """Prints a traceback without leaving it tangled in the live dashboard."""
    with ui.lock:
        ui._erase()
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        ui._paint()


def _worker(index, base_email):
    """Thread entry point: one browser, one instance, one row in the UI."""
    ui.bind(index)
    ui.worker_start(index)
    try:
        with sync_playwright() as p:
            # headless=True completely eliminates windows, taskbar icons, and GUI rendering overhead
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled"
                ]
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080}
                )
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://platform.acedata.cloud")
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://127.0.0.1:8082")
                install_captcha_hook(context)
                page = context.new_page()

                key = _flow(page, context, index, base_email)
                ui.worker_key(index, key)
                ui.worker_done(index, True, "key stored in free-claude-code and verified.")
                return key
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except KeyboardInterrupt:
        ui.worker_done(index, False, "cancelled by user.")
        return None
    except Exception as e:
        _show_traceback()
        ui.worker_done(index, False, f"{e.__class__.__name__}: {e}")
        return None


def ask_instance_count():
    ui.card("how many browsers?", [
        "Each browser runs on its own and earns its own API key.",
        f"Starts are {START_DELAY}s apart, so the Gmail codes never overlap.",
        f"Type 1-{MAX_INSTANCES}, or just press Enter for 1.",
    ])
    while True:
        answer = ui.ask("How many browsers at once?")
        if not answer:
            return 1
        if answer.isdigit() and 1 <= int(answer) <= MAX_INSTANCES:
            return int(answer)
        ui.warn(f"Enter a number between 1 and {MAX_INSTANCES}.")


def main():
    count = ask_instance_count()
    ui.begin(count, WORKER_STEPS)

    try:
        base_email = _bootstrap()
    except KeyboardInterrupt:
        ui.finish(False, "Cancelled by user.")
        return
    except Exception as e:
        _show_traceback()
        ui.finish(False, f"{e.__class__.__name__}: {e}")
        return

    if count > 1:
        ui.info(f"Setup done — launching {count} browsers, {START_DELAY}s apart.")
    else:
        ui.info("Setup done — launching the browser.")

    threads = []
    launched_at = time.time()
    try:
        for index in range(count):
            if index:
                target = launched_at + index * START_DELAY
                while time.time() < target:
                    ui.queue_note(index, f"starts in {int(round(target - time.time()))}s")
                    time.sleep(min(0.5, max(0.02, target - time.time())))
                ui.queue_note(index, "starting now")
            ui.worker_start(index)
            thread = threading.Thread(
                target=_worker,
                args=(index, base_email),
                name=f"ace-{index + 1}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)
    except KeyboardInterrupt:
        ui.skip_queued()
        ui.warn("Stopped launching new instances.")

    try:
        for thread in threads:
            # A plain join() would swallow Ctrl-C until every browser finished.
            while thread.is_alive():
                thread.join(0.4)
    except KeyboardInterrupt:
        ui.warn("Leaving the running instances in the background.")

    done = [w for w in ui.workers if w.status == "done"]
    failed = [w for w in ui.workers if w.status == "failed"]
    if threads and done and not failed and len(done) == len(threads):
        message = f"{len(done)} of {len(threads)} instances produced a key."
        success = True
    elif done:
        message = f"{len(done)} of {max(len(threads), 1)} instances produced a key."
        success = False
    else:
        message = "No keys were produced."
        success = False
    ui.finish(success, message)


if __name__ == "__main__":
    main()
