"""Ace Data Cloud -> free-claude-code key bridge."""
import io
import itertools
import json
import os
import platform
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


_GLYPHS = "\u2588\u2591\u25b8\u2714\u2716\u203a\u2022\u2026\u2192\u2500\u2584\u2580"
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
else:
    BAR_FULL, BAR_EMPTY = "#", "-"
    S_OK, S_ERR, S_WARN, S_INFO, S_NOTE, S_ARROW, S_ELL = "OK", "X", "!", ">", "*", ">", "..."

# Width of the banner art ("FREE CLAUDE" in half-block letters).
RULE_W = 41

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

def _fmt_duration(seconds):
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    return f"{minutes}m {sec:02d}s" if minutes else f"{sec}s"

def mask_key(key):
    return key if len(key) <= 12 else f"{key[:4]}{S_ELL}{key[-4:]}"

class UI:
    def __init__(self):
        self.lock = threading.RLock()
        self.plan = [("", 1)]
        self.total = 1
        self.total_weight = 1
        self.done_weight = 0
        self.index = 0
        self.fill = 0.0
        self.title = ""
        self.note = ""
        self.started_at = time.time()
        self.started = False
        self.live = LIVE
        try:
            self.cols = max(60, min(shutil.get_terminal_size((100, 24)).columns, 140))
        except Exception:
            self.cols = 100
        self.bar_w = 26 if self.cols >= 90 else 20

    def begin(self, plan=None):
        with self.lock:
            if plan is None or isinstance(plan, int):
                self.plan = [("", 1)] * max(1, int(plan or 1))
            else:
                self.plan = [(str(t), max(1, int(w))) for t, w in plan] or [("", 1)]
            self.total = len(self.plan)
            self.total_weight = sum(w for _, w in self.plan) or 1
            self.index = 0
            self.done_weight = 0
            self.fill = 0.0
            self.title = "Starting"
            self.note = ""
            self.started_at = time.time()
            self.started = True
            self._banner()
            self._draw()

    def step(self, title):
        with self.lock:
            if not self.started:
                return
            weight = 1
            for i in range(self.index, len(self.plan)):
                if self.plan[i][0] == title:
                    weight = self.plan[i][1]
                    break
            self.index = min(self.total, self.index + 1)
            self.done_weight = min(self.total_weight, self.done_weight + weight)
            self.title = title
            self.note = ""
            target = self.done_weight / self.total_weight
            if not self.live:
                self.fill = target
                print(self._line(target), flush=True)
                return
            start = self.fill
            for k in range(1, 11):
                self.fill = start + (target - start) * (k / 10.0)
                self._draw()
                time.sleep(0.008)
            self.fill = target
            self._draw()

    def set_note(self, text):
        with self.lock:
            self.note = text or ""
            self._draw()

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
        """Prints a command the user should copy, on its own highlighted line."""
        with self.lock:
            if COLOR:
                self._print(f"        {C.CYAN}{C.BOLD}{text}{C.RESET}")
            else:
                self._print(f"        {text}")

    def blank(self):
        with self.lock:
            self._print("")

    def key(self, value, title="API KEY", note=""):
        """Prints a captured secret in a box so it can be read straight off the terminal."""
        value = (value or "").strip()
        if not value:
            return
        with self.lock:
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

            if COLOR:
                frame = f"{C.PURPLE}{top}{C.RESET}"
            else:
                frame = top
            self._print(f"  {frame}")

            for chunk in chunks:
                pad = " " * (inner - len(chunk) - 2)
                if COLOR:
                    self._print(f"  {C.PURPLE}{bar}{C.RESET} {C.GREEN}{C.BOLD}{chunk}{C.RESET}{pad} "
                                f"{C.PURPLE}{bar}{C.RESET}")
                else:
                    self._print(f"  {bar} {chunk}{pad} {bar}")

            self._print(f"  {C.PURPLE}{bottom}{C.RESET}" if COLOR else f"  {bottom}")
            if note:
                self.detail(note)

    def ask(self, prompt):
        with self.lock:
            self._clear()
        answer = input(f"  {C.PURPLE}?{C.RESET}  {prompt} " if COLOR else f"  ?  {prompt} ")
        with self.lock:
            if self.started:
                self._draw()
        return answer.strip()

    def phase(self, title):
        with self.lock:
            pad = max(0, RULE_W - 4 - len(title))
            if COLOR:
                line = f"  {C.PURPLE}\u2500\u2500 {title} " + "\u2500" * pad + C.RESET
            else:
                line = f"  -- {title} " + "-" * pad
            self._print(line)

    def stop(self):
        with self.lock:
            self.started = False
            self._clear()

    def finish(self, success, message=""):
        with self.lock:
            self.started = False
            self._clear()
            elapsed = _fmt_duration(time.time() - self.started_at)
            rule = "\u2500" * RULE_W if UNICODE else "-" * RULE_W
            if success:
                head = f"{C.GREEN}{S_OK}{C.RESET}  COMPLETE" if COLOR else f"[{S_OK}]  COMPLETE"
            else:
                head = f"{C.RED}{S_ERR}{C.RESET}  STOPPED" if COLOR else f"[{S_ERR}]  STOPPED"
            print()
            print(f"  {C.PURPLE}{rule}{C.RESET}" if COLOR else f"  {rule}")
            if COLOR:
                print(f"  {head}   {C.DIM}\u00b7{C.RESET}  {C.BOLD}{elapsed}{C.RESET}")
            else:
                print(f"  {head}   -  {elapsed}")
            if message:
                print(f"     {C.DIM}{message}{C.RESET}" if COLOR else f"     {message}")
            print(f"  {C.PURPLE}{rule}{C.RESET}" if COLOR else f"  {rule}")
            print()

    def _banner(self):
        if UNICODE:
            print()
            print(f"{C.CYAN}  █▀▀ █▀█ █▀▀ █▀▀   █▀▀ █░░ ▄▀█ █░█ █▀▄ █▀▀{C.RESET}")
            print(f"{C.BLUE}  █▀░ █▀▄ ██▄ ██▄   █▄▄ █▄▄ █▀█ █▄█ █▄▀ ██▄{C.RESET}")
            print(f"{C.PURPLE}  " + "\u2500" * RULE_W + C.RESET)

            left, right = "Ace Data Cloud", "free-claude-code"
            arrow = "\u2192"
            slack = RULE_W - len(left) - len(right) - len(arrow)
            pad_l = max(1, slack // 2)
            pad_r = max(1, slack - pad_l)
            print(f"  {C.DIM}{left}{C.RESET}{' ' * pad_l}{C.PURPLE}{arrow}{C.RESET}"
                  f"{' ' * pad_r}{C.GREY}{right}{C.RESET}")
            print()
        else:
            left, right = "Ace Data Cloud", "free-claude-code"
            arrow = "->"
            slack = RULE_W - len(left) - len(right) - len(arrow)
            pad_l = max(1, slack // 2)
            pad_r = max(1, slack - pad_l)
            print()
            print("  FREE CLAUDE")
            print("  " + "-" * RULE_W)
            print(f"  {left}{' ' * pad_l}{arrow}{' ' * pad_r}{right}")
            print()

    def _line(self, fill):
        fill = max(0.0, min(1.0, fill))
        filled = int(round(self.bar_w * fill))
        pct = int(round(fill * 100))
        counter = f"{self.index:02d}/{self.total:02d}"
        prefix = f"  {S_ARROW} {counter}  "
        mid = f"  {pct:3d}%  "
        room = self.cols - len(prefix) - self.bar_w - len(mid) - 1
        title = self.title
        if len(title) > room:
            title = title[:max(8, room)]

        # Keep the step title intact and squeeze the note into whatever is left.
        note_plain = ""
        if self.note:
            avail = room - len(title) - 3
            if avail > 8:
                if len(self.note) <= avail:
                    text = self.note
                else:
                    text = self.note[:max(1, avail - len(S_ELL))] + S_ELL
                note_plain = "   " + text
        note = f"{C.DIM}{note_plain}{C.RESET}" if note_plain else ""
        if COLOR:
            bar = C.CYAN + BAR_FULL * filled + C.GREY + BAR_EMPTY * (self.bar_w - filled) + C.RESET
            return (f"  {C.PURPLE}{S_ARROW}{C.RESET} {C.GREY}{counter}{C.RESET}  "
                    f"{bar}  {C.BOLD}{pct:3d}%{C.RESET}  {C.WHITE}{title}{C.RESET}{note}")
        bar = BAR_FULL * filled + BAR_EMPTY * (self.bar_w - filled)
        return f"  {S_ARROW} {counter}  {bar}  {pct:3d}%  {title}{note_plain}"

    def _clear(self):
        if self.live:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()

    def _draw(self):
        if self.live and self.started:
            sys.stdout.write("\r\033[2K" + self._line(self.fill))
            sys.stdout.flush()

    def _print(self, text):
        self._clear()
        print(text, flush=True)
        if self.started:
            self._draw()

    def _icon(self, icon, color, msg):
        with self.lock:
            if COLOR:
                self._print(f"  {color}{icon}{C.RESET}  {msg}")
            else:
                self._print(f"  [{icon}] {msg}")

ui = UI()

# --- Dependency bootstrap ---
SCRIPT_DIR = BASE_DIR
CFT_KNOWN_GOOD = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)


def _stream(cmd, prefix):
    """Runs a command and mirrors its latest output line onto the status bar."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    last = ""
    for line in proc.stdout:
        line = line.strip()
        if line:
            last = line
            ui.set_note(f"{prefix}: {line[:80]}")
    code = proc.wait()
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
    ui.warn("pip is missing — bootstrapping it with ensurepip.")
    _stream([sys.executable, "-m", "ensurepip", "--upgrade"], "ensurepip")


def ensure_playwright():
    """Installs the playwright wheel when the import failed at startup."""
    global sync_playwright
    if sync_playwright is not None:
        ui.ok("Playwright package present.")
        return
    ui.warn("Playwright is not installed — installing the Python package (one time).")
    _ensure_pip()
    code, tail = _stream(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--progress-bar", "off", "playwright"],
        "pip",
    )
    if code != 0:
        raise Exception(f"pip could not install Playwright. Last output: {tail or 'unknown error'}")

    import importlib
    importlib.invalidate_caches()
    from playwright.sync_api import sync_playwright as _sync_playwright
    sync_playwright = _sync_playwright
    ui.ok("Playwright installed.")


def ensure_chromium():
    """Downloads Playwright's Chromium build when it is not on disk yet."""
    if sync_playwright is None:
        return
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        if exe and os.path.exists(exe):
            ui.ok("Chromium browser for Playwright present.")
            return
    except Exception:
        pass

    ui.warn("Chromium for Playwright is missing — downloading it (about 150 MB, one time).")
    code, tail = _stream([sys.executable, "-m", "playwright", "install", "chromium"], "chromium")
    if code != 0:
        raise Exception(f"Could not download Chromium. Last output: {tail or 'unknown error'}")
    ui.ok("Chromium installed.")


def _cft_platform():
    if sys.platform.startswith("win"):
        return "win64"
    if sys.platform == "darwin":
        return "mac-arm64" if platform.machine().lower() in ("arm64", "aarch64") else "mac-x64"
    return "linux64"


def ensure_chromedriver():
    """Optional safety net: Playwright drives Chromium itself, but keep a driver for Selenium-style tooling."""
    found = shutil.which("chromedriver")
    if found:
        ui.ok("chromedriver already on PATH.")
        return found

    target_dir = os.path.join(SCRIPT_DIR, "drivers")
    binary = "chromedriver.exe" if os.name == "nt" else "chromedriver"
    local = os.path.join(target_dir, binary)

    if os.path.exists(local):
        ui.ok(f"chromedriver present: {local}")
    else:
        ui.info("chromedriver not found — fetching the stable build.")
        try:
            plat = _cft_platform()
            with urllib.request.urlopen(CFT_KNOWN_GOOD, timeout=30) as resp:
                meta = json.load(resp)
            url = next(
                d["url"]
                for d in meta["channels"]["Stable"]["downloads"]["chromedriver"]
                if d["platform"] == plat
            )
            ui.set_note("chromedriver: downloading")
            with urllib.request.urlopen(url, timeout=120) as resp:
                blob = resp.read()
            os.makedirs(target_dir, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                member = next(n for n in zf.namelist() if n.endswith(binary))
                with zf.open(member) as src, open(local, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            if os.name != "nt":
                os.chmod(local, 0o755)
            ui.set_note("")
            ui.ok(f"chromedriver installed to {local}")
        except Exception as e:
            ui.set_note("")
            ui.warn(f"chromedriver skipped ({e.__class__.__name__}: {e}).")
            ui.detail("Not required here — Playwright talks to Chromium directly.")
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
        """Serves a plaintext view of all received verification codes at http://localhost:8080"""
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
        """Receives verification codes pushed from the Tampermonkey Gmail script"""
        global RECEIVED_CODES
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        try:
            data = json.loads(post_data.decode('utf-8'))
            code = data.get('code')
            if code:
                import datetime
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                with server_lock:
                    RECEIVED_CODES.append({"code": code, "time": timestamp})
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
        # Suppress default server access logs
        return

def start_local_server(port=BRIDGE_PORT):
    server = HTTPServer(("localhost", port), CodeReceiverHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

CONSUMED_CODES = set()

def wait_for_bridge_code(timeout_s=90):
    """Returns the first code the Tampermonkey bridge has not already handed over."""
    start = time.time()
    last = -1
    while time.time() - start < timeout_s:
        with server_lock:
            for item in RECEIVED_CODES:
                if item["code"] not in CONSUMED_CODES:
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

def discard_late_codes():
    """A code that lands after the account exists is a null token — report and ignore it."""
    with server_lock:
        late = [item for item in RECEIVED_CODES if item["code"] not in CONSUMED_CODES]
    for item in late:
        CONSUMED_CODES.add(item["code"])
        ui.warn(f"Late code {item['code']} arrived after account creation — ignored.")

# --- Account emails, passwords and API keys ---
def _read_email_lines(path):
    """Email-looking lines from a config/log file (junk like ff_email=... is skipped)."""
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
    """The last few accounts written to accounts.txt (newest last)."""
    return _read_email_lines(ACCOUNTS_FILE)[-ACCOUNT_HISTORY:]


def _used_emails():
    """Every email already spent: accounts.txt plus legacy variants in assets.txt."""
    used = set(_read_email_lines(ACCOUNTS_FILE))
    used.update(_read_email_lines(ASSETS_FILE))
    return used


def record_account_email(email):
    """Appends the account to accounts.txt, then keeps only the most recent few."""
    email = (email or "").strip().lower()
    if not email:
        return
    lines = _read_email_lines(ACCOUNTS_FILE)
    lines.append(email)
    lines = lines[-ACCOUNT_HISTORY:]
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception as e:
        ui.warn(f"Could not update {os.path.basename(ACCOUNTS_FILE)} ({e.__class__.__name__}).")


def dotted_sequence(username):
    """user, u.ser, us.er, use.r, u.s.er, u.se.r, us.e.r, ... (dots added left to right)."""
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
    """Returns (email or None, saw_after) for the first unused variant after `after`."""
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
    """Next free numbered alias (<username>100@..., <username>101@...)."""
    n = max(100, start)
    while n < 10000 and f"{username}{n}@{domain}" in used:
        n += 1
    return f"{username}{n}@{domain}"


def next_account_email(base_email):
    """Next account in the dot sequence, continuing after the newest in accounts.txt."""
    try:
        username, domain = base_email.strip().split("@", 1)
    except ValueError:
        raise Exception("Invalid email format provided.")

    username = username.lower()
    domain = domain.lower()
    recent = recent_accounts()
    used = _used_emails()
    last = recent[-1] if recent else None

    # Already past the dotted variants: keep counting numbered aliases.
    if last and last.endswith("@" + domain):
        match = re.match(rf"^{re.escape(username)}(\d+)$", last.split("@", 1)[0])
        if match:
            return _next_numbered(username, domain, used, int(match.group(1)) + 1)

    email, saw_last = _next_variant(username, domain, used, after=last)
    if email is None and last is not None and not saw_last:
        # The newest entry was not one of ours, so start the sequence again.
        email, _ = _next_variant(username, domain, used)
    if email is None:
        # Every dot combination is used up — fall back to numbered aliases.
        email = _next_numbered(username, domain, used)
    return email


def generate_password(length=18):
    """Random password with all four character classes and no look-alike glyphs."""
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
    """Appends the key to keys.txt (created on first use); the file is never rewritten."""
    key = (key or "").strip()
    name = os.path.basename(KEYS_FILE)
    if not key:
        return False
    try:
        if os.path.exists(KEYS_FILE):
            with open(KEYS_FILE, "r", encoding="utf-8", errors="replace") as f:
                if key in {line.strip() for line in f}:
                    ui.info(f"{name} already lists this key — nothing to add.")
                    return False
        with open(KEYS_FILE, "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except Exception as e:
        ui.warn(f"Could not write {name} ({e.__class__.__name__}).")
        return False
    ui.ok(f"API key saved to {name}.")
    return True


def ensure_output_files():
    """Creates keys.txt / accounts.txt up front so a long run cannot fail at the end."""
    for path in (KEYS_FILE, ACCOUNTS_FILE):
        name = os.path.basename(path)
        if os.path.exists(path):
            continue
        try:
            with open(path, "a", encoding="utf-8"):
                pass
        except Exception as e:
            ui.warn(f"Could not create {name} ({e.__class__.__name__}).")
            continue
        ui.detail(f"Created {name}.")


def _remember_base_email(email):
    """Adds a user-supplied base email to assets.txt so later runs find it."""
    name = os.path.basename(ASSETS_FILE)
    try:
        with open(ASSETS_FILE, "a", encoding="utf-8") as f:
            f.write((email or "").strip().lower() + "\n")
    except Exception as e:
        ui.warn(f"Could not write {name} ({e.__class__.__name__}).")


def get_base_regular_email():
    """Gets the base home email from assets.txt or prompts the user."""
    email_to_use = ""

    name = os.path.basename(ASSETS_FILE)
    if os.path.exists(ASSETS_FILE):
        lines = _read_email_lines(ASSETS_FILE)
        if lines:
            email_to_use = lines[0]

    if not email_to_use:
        ui.warn(f"No base email found in {name}.")
        email_to_use = ui.ask("Enter your regular home email (e.g. user@gmail.com):")
        if "@" not in email_to_use:
            raise Exception("That doesn't look like an email address.")
        _remember_base_email(email_to_use)
        ui.ok(f"Saved {email_to_use} to {name}.")

    return email_to_use


# --- hCaptcha solving (NoneCap API) ---
# The key lives in assets.txt as `nonecap=...` or `nonecapapi=...` (any case).
CAPTCHA_API_URL = "https://api.nonecap.com/v1/solves"
CAPTCHA_TIMEOUT = 180          # total budget for one solve, polling included
CAPTCHA_POLL_WAIT = 90         # seconds the API may hold a request open
CAPTCHA_WAIT = 12              # seconds to let the widget render before giving up
CAPTCHA_CLICK_TIMEOUT = 1500   # ms spent on each checkbox selector

# The checkbox widget lives in a cross-origin iframe, but Playwright can still
# drive it. The challenge frame must not match, or frame_locator hits strict mode.
CAPTCHA_FRAME_SELECTOR = (
    'iframe[src*="frame=checkbox"], iframe[src*="hcaptcha.com"][title*="checkbox" i]'
)

CAPTCHA_INFO_JS = r"""() => {
    const FRAME_SEL = __FRAME_SEL__;

    // hCaptcha is often mounted inside a React portal or a shadow root.
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
            // hCaptcha puts the sitekey in the fragment, not the query string:
            //   hcaptcha.html#frame=checkbox&...&sitekey=663c73ac-...
            const hash = parsed.hash ? parsed.hash.slice(1) : '';
            return new URLSearchParams(hash).get('sitekey');
        } catch (e) {
            return null;
        }
    };

    // 1) the container attribute, 2) the widget iframe URL, 3) hCaptcha's own
    // checksiteconfig request recorded in the resource timeline (this is the one
    // that works for React-driven widgets that render no data-sitekey at all).
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

    // 0) The widget's own reader. vue-hcaptcha (this site's wrapper) calls
    //    hcaptcha.getResponse(widgetId) inside its verify callback, so answering
    //    that is what makes the app submit the real token. Once
    //    __fccCaptchaToken is cleared for a retry the original is used again.
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

    // 1) The hidden field hCaptcha normally fills in itself.
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

    // 2) Plain <div data-callback="fn"> integrations.
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

    // 3) React / Vue integrations never read the DOM — they keep the token in
    //    component state. Walk up the React fiber tree from the widget and call
    //    the verification props (onSuccess / onVerify / callback / ...).
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

    // 4) hCaptcha's own render callback, captured by the init hook. This is the
    //    one Vue wrappers (vue-hcaptcha) actually listen to — firing it sets the
    //    form's captcha field and lets the page submit by itself.
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

# Installed before the page loads hCaptcha. Wrappers like vue-hcaptcha hand
# their onVerify callback to hcaptcha.render(); keeping a copy of it is the only
# reliable way to deliver an API token to the app's own form state.
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

    // api.js assigns window.hcaptcha first and defines render a moment later,
    // so the hook has to patch the object — patching the assignment misses it.
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
    """Adds the hCaptcha callback hook to a browser context (all its pages)."""
    try:
        context.add_init_script(CAPTCHA_HOOK_JS)
    except Exception as e:
        ui.detail(f"Captcha callback hook not installed ({e.__class__.__name__}).")


def _nonecap_key():
    """The NoneCap API key from assets.txt (`nonecap=...` / `nonecapapi=...`)."""
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
    """One NoneCap request. Returns (json_dict, http_status, error_text)."""
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
    """Solves one hCaptcha. Returns (token, error, info_dict).

    POST /v1/solves holds the connection up to CAPTCHA_POLL_WAIT seconds; if the
    solve is still running the API answers 202 and the returned id is polled.
    """
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
    """Runs the blocking solve on a thread so the bar can show elapsed time."""
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
    """Uses the solver's browser version for later requests — some sites bind
    the token to the User-Agent that solved it."""
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
    """Reads the widget state from the page (None if the page is not ready)."""
    js = CAPTCHA_INFO_JS.replace("__FRAME_SEL__", json.dumps(CAPTCHA_FRAME_SELECTOR))
    try:
        info = page.evaluate(js)
    except Exception:
        return None
    return info if isinstance(info, dict) else None


def _wait_for_captcha(page, timeout=None):
    """Polls until the widget has actually rendered — it loads asynchronously."""
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
    """Drops a token we injected earlier so a retry gets a fresh one."""
    try:
        cleared = page.evaluate(CAPTCHA_CLEAR_JS)
    except Exception:
        return 0
    return cleared or 0


def _try_captcha_checkbox(page):
    """Clicks the widget's own checkbox. Free, and the page sees a genuine solve."""
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
    """Clicks the checkbox and reports whether the widget itself produced a token."""
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
    """Solves the page's hCaptcha.

    Returns a token or a short status ("already-solved", "clicked", "absent"),
    or None when it could not be solved. `force` ignores a token we injected
    ourselves so a retry after a failed submit gets a fresh one. `click_first`
    tries the widget's own checkbox before spending an API credit.
    """
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
    """Fills a fresh dotted email variant until the site confirms the code was sent."""
    start_time = time.time()
    error_logged = False

    while time.time() - start_time < 300:
        target_email = next_account_email(base_email)
        record_account_email(target_email)
        ui.info(f"Trying email: {target_email}")

        email_input = page.locator('input[placeholder="Email"]')
        email_input.wait_for(state="visible", timeout=10000)
        email_input.click()
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        email_input.fill(target_email)

        # The hCaptcha widget only renders *after* this click, and the page
        # sends the code by itself once verification completes.
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
                        # No widget after this long: this build does not need one.
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

    // The Ace UI shows:  [Bearer] c978...a559f134d352  v  [copy]
    // "Bearer" is its own element, so the key value/text has NO "Bearer " prefix.
    // Match the token itself instead (masked, or a long opaque secret). App IDs are skipped.
    const keyish = (s) => {
        const t = norm(s).replace(/^Bearer\s+/i, '').trim();
        if (!t) return false;
        if (UUID.test(t) || /^[0-9a-f]{8}-[0-9a-f]{4}/i.test(t)) return false;
        if (!/^[A-Za-z0-9._\-]{2,}$/.test(t)) return false;
        return isMasked(t) || (t.length >= 18 && t.length <= 128);
    };

    let rect = null;

    // 1) A real input/textarea holding the key.
    for (const el of document.querySelectorAll('input, textarea')) {
        if (!keyish(el.value || '')) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 60 || r.height < 14) continue;
        rect = r;
        break;
    }

    // 2) The key rendered as text (Ant Design <Select>/span).
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
        // Skip the Select dropdown chevron; we want the copy/eye control.
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

    // Named/titled copy controls first...
    for (const el of document.querySelectorAll(
        '[aria-label*="copy" i], [title*="copy" i], .anticon-copy, [class*="copy" i], [data-icon="copy"]'
    )) add(el, 'attr');

    // ...then any small icon to the right of the key (the copy glyph's geometry).
    for (const svg of document.querySelectorAll('svg')) add(svg, 'geom');

    // Rightmost first within each group -> copy/eye glyph beats the chevron.
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

# Reads a full (unmasked) key straight out of the DOM, in case an eye/reveal
# control already exposed it in the field.
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
    """Polls the clipboard until it differs from `marker`; returns the trimmed value."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        cur = read_clipboard(page)
        if cur and cur != marker:
            return re.sub(r"^Bearer\s+", "", cur, flags=re.IGNORECASE).strip()
        page.wait_for_timeout(150)
    return ""

def _usable_key(cand):
    return bool(cand) and not is_masked(cand) and not APPID_RE.match(cand) and len(cand) >= 16

def _dump_key_dom(page):
    """Prints the nearby key-ish elements so a failed lookup is diagnosable."""
    try:
        rows = page.evaluate(r"""() => {
            const out = [];
            for (const el of document.querySelectorAll('input, textarea, span, div, button')) {
                const raw = el.value || (el.children.length ? '' : el.textContent) || '';
                const t = (raw || '').replace(/\s+/g, ' ').trim();
                if (!t || t.length > 90) continue;
                if (!/Bearer|key|\.{3}|[0-9a-f]{6}/i.test(t)) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 10 || r.height < 10) continue;
                out.push({
                    tag: el.tagName, type: el.type || '', text: t.slice(0, 70),
                    cls: (el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className) || '',
                    x: Math.round(r.x), y: Math.round(r.y),
                    w: Math.round(r.width), h: Math.round(r.height)
                });
                if (out.length >= 20) break;
            }
            return out;
        }""")
        for row in rows or []:
            ui.detail(f"{row['tag']:<6} {row['type']:<8} ({row['x']},{row['y']}) "
                      f"{row['w']}x{row['h']}  {row['text']}  [{str(row['cls'])[:40]}]")
    except Exception as e:
        ui.detail(f"dump failed: {e}")

def grab_ace_api_key(ace_page):
    """Clicks the copy glyph to the right of the API key field, then reads the clipboard."""
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

        rect = (info or {}).get("rect")
        cands = (info or {}).get("candidates") or []

        if rect:
            ui.info(f"API key field at ({rect['x']:.0f}, {rect['y']:.0f}) — {len(cands)} copy candidate(s).")
        else:
            ui.warn("Could not find the API key field in the DOM.")

        for i, c in enumerate(cands):
            ui.info(f"Clicking copy glyph #{i + 1} at ({c['x']:.0f}, {c['y']:.0f}) [{c['how']}].")
            try:
                clicked = bool(ace_page.evaluate(ACE_CLICK_COPY_JS, i))
            except Exception:
                clicked = False

            cand = _wait_for_clipboard_change(ace_page, marker, 2.5) if clicked else ""

            # A DOM click can land on an svg wrapper instead of the handler;
            # retry the same spot with a real mouse click.
            if not _usable_key(cand):
                try:
                    ace_page.mouse.click(c["x"], c["y"])
                except Exception:
                    pass
                mouse_cand = _wait_for_clipboard_change(ace_page, marker, 2.0)
                if _usable_key(mouse_cand):
                    cand = mouse_cand

            # Some copy controls reveal the plaintext in the field instead of copying.
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
            if cand:
                ui.warn(f"Glyph #{i + 1} returned {cand[:14]}{S_ELL} — not the full key.")

        if key:
            break

        ui.warn(f"Copy attempt {attempt} produced no usable key.")
        ace_page.wait_for_timeout(600)

    if not key:
        ui.err("Could not read a full key from any glyph near the API key field.")
        ui.detail("Dumping the surrounding DOM:")
        _dump_key_dom(ace_page)
        while not key:
            manual = ui.ask("Paste the full Ace Data Cloud API key:")
            manual = re.sub(r"^Bearer\s+", "", manual, flags=re.IGNORECASE).strip()
            if not manual or is_masked(manual) or len(manual) < 16:
                ui.warn("Still masked or too short — paste the full key, not the shortened display.")
            elif APPID_RE.match(manual):
                ui.warn("That's an App ID, not the API key.")
            else:
                key = manual

    ui.ok(f"API key captured ({len(key)} chars).")
    _store_on_clipboard(ace_page, key)
    ui.key(key, "API KEY", "Full key printed above and copied to the clipboard.")
    return key


def _store_on_clipboard(page, key):
    """Best-effort: puts the key on the OS clipboard so it can be pasted by hand."""
    try:
        wrote = page.evaluate(
            """async (v) => {
                try { await navigator.clipboard.writeText(v); return true; }
                catch (e) { return false; }
            }""",
            key,
        )
    except Exception:
        wrote = False

    if wrote:
        ui.detail("Clipboard now holds the full key.")
    else:
        ui.warn("Could not write the key to the clipboard — copy it from the box above.")


# --- Free credit claim ---
EARN_CREDIT_PATTERNS = (
    re.compile(r"earn\s*\d*\s*credits?", re.IGNORECASE),
    re.compile(r"earn.{0,12}free|free.{0,12}credit", re.IGNORECASE),
    re.compile(r"claim.{0,15}credit|receive.{0,15}credit|acquire", re.IGNORECASE),
)
EARN_APPLY_RE = re.compile(r"^\s*Apply\s*$", re.IGNORECASE)


def _first_visible(locator, limit=25):
    """First visible element in a locator, or None."""
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
    """Finds the 'earn 1 credit for free' style control on the docs page."""
    scopes = ("button:visible, a:visible, [role='button']:visible", "span:visible")
    for pattern in EARN_CREDIT_PATTERNS:
        for scope in scopes:
            el = _first_visible(page.locator(scope).filter(has_text=pattern))
            if el is not None:
                return el
    return None


CREDIT_OK_RE = re.compile(r"success|claimed|credited|added to your account", re.IGNORECASE)


def _credit_confirmed(page, timeout=0):
    """True when the page shows a success message (uses rendered text only)."""
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

    // Smallest matching element = the label/title of the card; prefer real controls.
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
    """Clicks the 'Dedicated Balance' button/card. Never fatal."""
    ui.info("Looking for the 'Dedicated Balance' button...")
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
        ui.warn("No 'Dedicated Balance' button on the page — skipping that click.")
        return False

    try:
        target = page.locator('[data-fcc-dedicated="1"]').first
        target.scroll_into_view_if_needed()
        target.click(force=True)
    except Exception as e:
        ui.warn(f"Could not click 'Dedicated Balance' ({e.__class__.__name__}).")
        return False

    page.wait_for_timeout(800)
    ui.ok(f"Opened '{label}'.")
    return True


def claim_free_credit(page, select_dedicated=True):
    """Opens the Claude docs page and claims the free credit (earn free credit -> Apply)."""
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
        path = debug_shot(page, "credit-earn")
        ui.warn(f"No 'earn free credit' control on that page (screenshot: {path}).")
        return False

    label = ""
    try:
        label = " ".join((earn.inner_text() or "").split())[:60]
    except Exception:
        pass
    ui.info(f"Clicking '{label}'..." if label else "Clicking the free-credit control...")
    try:
        earn.scroll_into_view_if_needed()
        earn.click(force=True)
    except Exception as e:
        path = debug_shot(page, "credit-earn")
        ui.warn(f"Could not click the free-credit control ({e.__class__.__name__}; screenshot: {path}).")
        return False

    page.wait_for_timeout(1800)

    apply_btn = _first_visible(page.locator("button:visible").filter(has_text=EARN_APPLY_RE))
    if apply_btn is None:
        apply_btn = _first_visible(page.locator('text="Apply"'))
    if apply_btn is None:
        if _credit_confirmed(page):
            ui.ok("Free credit claimed (no Apply step needed).")
            if select_dedicated:
                select_dedicated_balance(page)
            return True
        path = debug_shot(page, "credit-apply")
        ui.warn(f"No 'Apply' button appeared for the free credit (screenshot: {path}).")
        return False

    ui.info("Clicking 'Apply'...")
    try:
        apply_btn.scroll_into_view_if_needed()
        apply_btn.click(force=True)
    except Exception as e:
        path = debug_shot(page, "credit-apply")
        ui.warn(f"Could not click 'Apply' ({e.__class__.__name__}; screenshot: {path}).")
        return False

    if _credit_confirmed(page, 12):
        ui.ok("Free credit claimed.")
    else:
        ui.warn("'Apply' clicked, but no confirmation text appeared — check the credit balance.")

    if select_dedicated:
        select_dedicated_balance(page)
    return True


def fill_field(page, el, value, label, settle_ms=400):
    """Fills a field, then re-reads it so a React reset is never mistaken for success."""
    def current():
        try:
            return (el.input_value() or "").strip()
        except Exception:
            return ""

    el.scroll_into_view_if_needed()
    if current() == value:
        ui.ok(f"{label} already set.")
        return

    try:
        el.fill(value, timeout=10000)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)
    if current() == value:
        ui.ok(f"{label} set.")
        return

    ui.warn(f"Direct fill did not stick on {label} — typing it instead.")
    try:
        el.click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(value, delay=20)
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)
    if current() == value:
        ui.ok(f"{label} set (typed).")
        return

    ui.warn(f"Keystrokes did not stick on {label} — writing the value directly.")
    try:
        page.evaluate("""([el, val]) => {
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, val);
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        }""", [el.element_handle(), value])
    except Exception:
        pass
    page.wait_for_timeout(settle_ms)

    if current() != value:
        raise Exception(f"Could not set {label} (the field keeps resetting).")
    ui.ok(f"{label} set (direct write).")


def debug_shot(page, name):
    """Saves a screenshot next to the script so a failed step can be inspected later."""
    path = os.path.join(SCRIPT_DIR, f"debug_{name}.png")
    try:
        page.screenshot(path=path)
        ui.warn(f"Screenshot saved: {path}")
    except Exception:
        pass
    return path

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
    """Opens (or reuses) the free-claude-code admin page and waits for it to render."""
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


def explain_fcc_setup():
    """Tells the user how to install and launch free-claude-code."""
    ui.detail("free-claude-code is not answering, so it is probably not installed or not running.")
    ui.detail("To install it, run this in PowerShell:")
    ui.blank()
    ui.cmd(FCC_INSTALL_CMD)
    ui.blank()
    ui.detail('Then open the Windows search bar, type "free-claude-code" and launch it.')
    ui.detail("It starts a local server and serves the admin panel on port 8082.")


def prompt_fcc_running(context):
    """Keeps trying the pre-opened admin tab, guiding the user to start FCC. None = skipped."""
    admin = None
    while True:
        try:
            admin = open_fcc_admin(context, admin)
            return admin
        except Exception as e:
            ui.err(str(e))
            answer = ui.ask("Is free-claude-code installed and running? [y/N]")
            if answer.strip().lower() in ("n", "no"):
                explain_fcc_setup()

            choice = ui.ask("Press Enter to retry once it is running (type 'skip' to carry on without it)")
            if choice.strip().lower() in ("skip", "s"):
                ui.warn("Continuing without the admin panel — the key will be printed so you can add it later.")
                return None
            ui.info("Retrying the admin panel...")


def reuse_or_open_admin(context, admin):
    """Brings the pre-opened admin tab back and refreshes it, re-opening if it died."""
    if admin is None or admin.is_closed():
        return prompt_fcc_running(context)
    try:
        admin.bring_to_front()
        admin.reload(wait_until="domcontentloaded", timeout=30000)
        admin.wait_for_selector("text=/custom providers/i", timeout=15000)
        ui.ok("Admin panel tab refreshed.")
        return admin
    except Exception:
        ui.warn("The admin tab stopped responding — opening it again.")
        return prompt_fcc_running(context)


def _label_field(scope, label):
    """Finds the input that belongs to a visible field label inside `scope`."""
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

    // Smallest ancestor of the last key field that also owns a Save button.
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
    """Tags and returns the provider editor container (dialog fallback, then the page)."""
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
    """True once the provider editor is no longer on screen."""
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
    """Polls until the editor disappears or the timeout runs out."""
    deadline = time.time() + max(0.0, seconds)
    while True:
        if _editor_closed(page):
            return True
        if time.time() >= deadline:
            return False
        page.wait_for_timeout(200)


def _close_editor(page, seconds=6.0):
    """Clicks Cancel in the editor and waits for it to go away."""
    try:
        cancel = _editor_button(page, FCC_CANCEL_RE)
        if not cancel.count():
            ui.warn("No 'Cancel' button in the editor.")
            return False
        cancel.scroll_into_view_if_needed()
        cancel.click(force=True)
    except Exception as e:
        ui.warn(f"Cancel click failed ({e.__class__.__name__}).")
        return False
    return _wait_editor_closed(page, seconds)


def _editor_button(page, pattern):
    """Finds a button inside the editor first, then anywhere on the page."""
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
        // Stop once the container stops looking like a single row.
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
    """True when this field sits in a row that has its own Remove/Delete button."""
    try:
        return bool(el.evaluate(ROW_REMOVE_JS))
    except Exception:
        return False


def _key_rows(scope):
    """Visible API-key fields inside the editor, top to bottom."""
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
            # Builds that show the stored key masked inside the field instead of a hint.
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
    text = _row_text(el)
    return "configured" in text or "replace" in text


def _click_add_key(page, scope):
    """Clicks 'Add key' inside the editor and waits for a new blank row to appear."""
    before = len(_key_rows(scope))
    add = scope.locator("button:visible").filter(has_text=FCC_ADD_KEY_RE)
    if not add.count():
        add = page.locator("button:visible").filter(has_text=FCC_ADD_KEY_RE)
    if not add.count():
        ui.warn("No 'Add key' button found in the editor.")
        return None

    btn = add.first
    btn.scroll_into_view_if_needed()
    ui.info("Clicking 'Add key'.")

    for attempt in range(1, 4):
        try:
            btn.click(force=True)
        except Exception as e:
            ui.warn(f"'Add key' click failed ({e.__class__.__name__}).")

        deadline = time.time() + 5
        while time.time() < deadline:
            rows = _key_rows(scope)
            fresh = [r for r in rows if not _row_is_stored(r)]
            if len(rows) > before and fresh:
                ui.ok(f"Key row added ({before} \u2192 {len(rows)}).")
                return fresh[-1]
            page.wait_for_timeout(250)
        ui.warn(f"No new key row after click {attempt}/3.")

    return None


def _row_is_stored(el):
    """A row counts as an existing key if it is marked configured OR already carries a value."""
    if _row_is_configured(el):
        return True
    return not _row_is_blank(el)


def _stored_key_count(rows):
    """How many keys the provider already has (stored rows, not blank entry slots)."""
    return sum(1 for r in rows if _row_is_stored(r))


def _type_key_into(page, target, ace_key):
    """Fills a key row and proves the value survived a re-render."""
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
    ui.ok(f"Key verified in the field ({len(held)} chars).")


def _set_provider_key(page, ace_key):
    """Adds the Ace key as a NEW key row — a stored key is never overwritten."""
    scope = _editor_scope(page)
    rows = _key_rows(scope)
    before = _stored_key_count(rows)
    ui.ok(f"API keys already configured: {before}")
    ui.info(f"Key fields in the editor right now: {len(rows)} (blank slots: {len(rows) - before}).")

    blank = [r for r in rows if not _row_is_stored(r)]
    target = blank[-1] if blank else None
    if target is not None:
        ui.ok("Using the empty key slot — no stored key is touched.")
    else:
        if before:
            ui.ok("Every row already holds a stored key — adding a fresh row.")
        else:
            ui.info("No key rows yet — adding the first one.")
        target = _click_add_key(page, scope)

    if target is not None:
        try:
            _type_key_into(page, target, ace_key)
            return before
        except Exception as e:
            ui.warn(str(e))

    added = _click_add_key(page, scope)
    if added is None:
        path = debug_shot(page, "key-entry")
        raise Exception(f"The key would not stay in any key field (screenshot: {path}).")

    try:
        _type_key_into(page, added, ace_key)
    except Exception as e:
        path = debug_shot(page, "key-entry")
        raise Exception(f"{e} (screenshot: {path})")

    return before


def _open_provider_editor(page):
    """Clicks Edit on the ace data cloud card, adding the provider when it is not there yet."""
    if page.evaluate(FCC_FIND_CARD_JS, PROVIDER_NAME):
        card = page.locator('[data-fcc-card="1"]').first
        card.scroll_into_view_if_needed()
        edit = card.locator("button:visible").filter(has_text=FCC_EDIT_RE).first
        if not edit.count():
            edit = card.locator("button").filter(has_text=FCC_EDIT_RE).first
        edit.click(force=True)
        ui.ok("Found the existing 'ace data cloud' provider.")
        return False

    ui.info("No 'ace data cloud' provider yet — adding one.")
    add = page.locator("button:visible").filter(has_text=FCC_ADD_PROVIDER_RE).first
    if not add.count():
        add = page.locator("button").filter(has_text=FCC_ADD_PROVIDER_RE).first
    add.wait_for(state="visible", timeout=10000)
    add.scroll_into_view_if_needed()
    add.click(force=True)
    return True


def configure_ace_provider(page, ace_key):
    """Edits or creates the Ace provider and saves the captured key.

    Returns (saved, keys_before, None); an existing key is never overwritten.
    """
    is_new = _open_provider_editor(page)

    # The editor animates in; wait for its Save button before poking at fields.
    try:
        page.locator("button:visible").filter(has_text=FCC_SAVE_RE).first.wait_for(
            state="visible", timeout=15000
        )
    except Exception:
        path = debug_shot(page, "editor-missing")
        raise Exception(f"The provider editor did not open (screenshot: {path}).")

    scope = _editor_scope(page)

    if is_new:
        fill_field(page, _label_field(scope, "Name"), PROVIDER_NAME, "Provider name")
        fill_field(page, _label_field(scope, "Base URL"), PROVIDER_BASE_URL, "Base URL")
    else:
        ui.ok("Using the existing provider entry — only a new key is added.")

    ui.step("Enter API key")
    before = _set_provider_key(page, ace_key)

    ui.step("Save provider")
    save = _editor_button(page, FCC_SAVE_RE)
    try:
        save.scroll_into_view_if_needed()
        save.click(force=True)
    except Exception as e:
        path = debug_shot(page, "save-click")
        raise Exception(f"Could not click 'Save' ({e.__class__.__name__}; screenshot: {path}).")

    ui.info("Save clicked — waiting up to 3s for the editor to auto-close...")
    closed = _wait_editor_closed(page, 3.0)

    if closed:
        ui.ok("Editor closed on its own.")
    else:
        ui.info("Editor stayed open — clicking Cancel to close it.")
        closed = _close_editor(page, 6.0)
        if closed:
            ui.ok("Editor closed with Cancel (the key was already saved).")
        else:
            path = debug_shot(page, "provider-save")
            ui.warn(f"Could not close the provider editor (screenshot: {path}).")
            return False, before, None

    ui.ok("Provider saved.")
    # The panel's own "Saved" state is trusted: re-opening the editor to recount
    # keys is slow and often times out on real installs, so we skip it.
    return True, before, None


def apply_pending_changes(page):
    """Clicks the footer 'Apply' button once it becomes enabled (turns green).

    Only waits 2s: on real installs Save already applies the change, so a
    greyed-out Apply just means there is nothing left to push.
    """
    apply_btn = page.locator("button:visible").filter(has_text=FCC_APPLY_RE).last
    if not apply_btn.count():
        ui.warn("No 'Apply' button at the bottom — nothing to apply.")
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
        enabled = apply_btn.is_enabled()
    except Exception:
        enabled = False

    if not enabled:
        ui.warn("'Apply' is greyed out — the app reports no pending changes (the key is already stored).")
        return False

    apply_btn.scroll_into_view_if_needed()
    apply_btn.click(force=True)
    page.wait_for_timeout(1800)
    ui.ok("Changes applied.")
    return True

def _registration_form_ready(page, timeout=0):
    """True once the register form (a password field) is on screen."""
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

    // Prefer a real tab/button over a wrapper div of the same text.
    hits.sort((a, b) => rank(a) - rank(b) || area(a) - area(b));
    const pick = hits[0];
    document.querySelectorAll('[data-fcc-register="1"]').forEach((el) => el.removeAttribute('data-fcc-register'));
    pick.setAttribute('data-fcc-register', '1');
    return flat(pick.innerText || pick.textContent);
}"""


def _select_register_tab(page):
    """Clicks the visible 'Register' tab — never a hidden submit button."""
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
    """Navigates, retrying once so a failed load cannot cancel the next goto."""
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
    ui.warn(f"Could not open {url} ({last.__class__.__name__}).")
    return False


def _docs_register_route(page):
    """Old path: docs page -> Receive credit -> Apply -> register form."""
    page.wait_for_timeout(1500)
    page.locator('text=/Receive.*Credit|Acquire/i').first.click(timeout=15000)
    page.locator('text=Apply').first.click(timeout=15000)
    page.wait_for_selector('input[type="password"]', timeout=15000)
    _select_register_tab(page)
    return True


def open_registration(page):
    """Goes straight to the Ace register page, falling back to the docs route."""
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
        ui.warn("The register page showed no form — using the docs route instead.")

    _goto(page, TARGET_URL)
    _docs_register_route(page)
    ui.ok("Registration form ready.")
    return True


def _run():
    try:
        ui.phase("BOOTSTRAP")
        ui.step("Check dependencies")
        ensure_environment()

        ui.step("Load configuration")
        base_email = get_base_regular_email()
        ui.ok(f"Base email: {base_email}")
        ensure_output_files()
        ui.detail(f"API keys -> {KEYS_FILE}")
        ui.detail(f"Recent accounts -> {ACCOUNTS_FILE}")

        ui.step("Start Gmail bridge")
        try:
            start_local_server(BRIDGE_PORT)
            ui.ok(f"Bridge listening on http://localhost:{BRIDGE_PORT}")
        except OSError as e:
            ui.warn(f"Port {BRIDGE_PORT} busy — reusing an existing bridge. ({e})")
        ui.info("Keep the Tampermonkey Gmail tab signed in.")

        if sync_playwright is None:
            raise Exception("Playwright is unavailable — run: pip install playwright")

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context()
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://platform.acedata.cloud")
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://127.0.0.1:8082")
            install_captcha_hook(context)
            page = context.new_page()

            prompt_close = True
            admin = None
            try:
                ui.step("Check admin panel")
                admin = prompt_fcc_running(context)
                if admin is not None:
                    ui.ok(f"free-claude-code is up at {FCC_ADMIN_URL}")
                    page.bring_to_front()

                ui.phase("ACE DATA CLOUD")
                ui.step("Open registration page")
                page.bring_to_front()
                open_registration(page)

                ui.step("Request verification code")
                send_btn = page.locator('input[placeholder*="Verification"] + button, div:has-text("Email Verification Code") button').first
                if not send_btn.count():
                    send_btn = page.locator('button').filter(has_text=re.compile("Send Email Code|s$", re.IGNORECASE)).first
                send_btn.wait_for(state="visible", timeout=10000)
                used_email = submit_email_and_wait_for_success(page, send_btn, base_email)
                ui.ok(f"Verification code sent to {used_email}")

                ui.step("Receive verification code")
                ui.info("Waiting for the local Gmail bridge...")
                code = wait_for_bridge_code(90)
                if not code:
                    raise Exception("Timed out waiting for the Gmail verification code via the local bridge.")
                ui.ok("Verification code accepted.")

                ui.step("Fill registration form")
                password = generate_password()
                page.bring_to_front()
                page.fill('input[placeholder*="Verification"]', code)
                page.fill('input[placeholder="Password"]', password)
                ui.ok("Email code and password filled in.")
                ui.detail(f"Password used: {password}")

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
                    ui.warn("Distinct submit button not found — using the general selector.")
                    page.click('button:has-text("Register")')
                ui.ok("Registration submitted.")

                ui.step("Open console")
                page.wait_for_selector("text=Console", timeout=15000)
                page.click("text=Console")
                discard_late_codes()
                ui.ok("Console open.")

                ui.step("Open API key dialog")
                ui.info("Looking for the API key control...")
                ui.set_note("create-key 10s")
                try:
                    page.wait_for_selector("text=Create API Key", timeout=10000)
                    page.click("text=Create API Key")
                    ui.ok("API key dialog opened.")
                except Exception:
                    ui.warn("No 'Create API Key' control — using the existing console key.")
                finally:
                    ui.set_note("")
                page.wait_for_timeout(2500)

                ui.step("Capture API key")
                ace_key = grab_ace_api_key(page)

                ui.step("Save key to keys.txt")
                save_api_key(ace_key)

                ui.phase("FREE-CLAUDE-CODE")
                ui.step("Open provider settings")
                admin = reuse_or_open_admin(context, admin)
                if admin is None:
                    raise Exception(
                        "free-claude-code is not running, so the key could not be stored — "
                        "it is printed above so you can add it by hand."
                    )

                saved, keys_before, keys_after = configure_ace_provider(admin, ace_key)

                ui.step("Apply changes")
                applied = apply_pending_changes(admin)

                ui.phase("ACE DATA CLOUD")
                ui.step("Claim free credit")
                claimed = claim_free_credit(page, select_dedicated=False)

                ui.step("Open Dedicated Balance")
                dedicated = select_dedicated_balance(page)

                ui.stop()
                if not saved:
                    return False, (
                        f"Ace key {ace_key} was captured but the admin panel did not confirm the save — "
                        "the key is printed above so you can paste it by hand."
                    )

                if keys_after is not None and keys_after > keys_before:
                    detail = f"key added ({keys_before} \u2192 {keys_after} keys, existing ones untouched)"
                elif keys_after is not None and keys_after == keys_before:
                    detail = f"key count unchanged at {keys_after} — check the panel"
                else:
                    detail = f"key added (the {keys_before} existing key(s) were left untouched)"

                credit = " Free credit claimed." if claimed else ""
                if dedicated:
                    credit += " Dedicated balance open."
                if applied:
                    return True, f"Ace key {ace_key} stored in free-claude-code and applied — {detail}.{credit}"
                return True, f"Ace key {ace_key} stored in free-claude-code — {detail}.{credit}"

            except KeyboardInterrupt:
                prompt_close = False
                ui.stop()
                return False, "Cancelled by user."
            except Exception as e:
                ui.stop()
                traceback.print_exc()
                return False, f"{e.__class__.__name__}: {e}"
            finally:
                if prompt_close:
                    try:
                        ui.ask("Press Enter to close the browser...")
                    except Exception:
                        pass
                try:
                    browser.close()
                except Exception:
                    pass

    except KeyboardInterrupt:
        ui.stop()
        return False, "Cancelled by user."
    except Exception as e:
        ui.stop()
        traceback.print_exc()
        return False, f"{e.__class__.__name__}: {e}"


def main():
    ui.begin(STEPS)
    success, message = _run()
    ui.finish(success, message)


if __name__ == "__main__":
    main()
