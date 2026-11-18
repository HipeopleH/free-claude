#!/usr/bin/env python3
"""
NoneCap onboarding — patchright + system Chrome.

No 1.3 GB browser download. patchright is a driver-level hardened build of
Playwright: it strips the Runtime.enable / Console.enable CDP leaks and the
navigator.webdriver tell, and launches the Chrome already on the machine.
Turnstile is clicked for real inside its iframe, which is enough when the
fingerprint isn't obviously automated.

Deps:
  pip install patchright

Run:
  python nonecap_onboard.py
  python nonecap_onboard.py --headless
  python nonecap_onboard.py --keep-open
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import secrets
import shutil
import string
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Iterable, Optional

from patchright.async_api import (
    async_playwright,
    BrowserContext,
    Frame,
    Locator,
    Page,
    TimeoutError as PWTimeout,
)

# ---------------------------------------------------------------- config block

SIGNUP_URL = "https://dashboard.nonecap.com/signup"
LOGIN_URL = "https://dashboard.nonecap.com/login"
MAIL_URL = "https://tempmail.lol/en"

PROFILE_DIR = Path(os.environ.get("LOCALAPPDATA", ".")) / "nonecap-chrome-profile"
REAL_CHROME_PROFILE = (
    Path(os.environ.get("LOCALAPPDATA", ".")) / "Google" / "Chrome" / "User Data"
)
CLONE_PROFILE = True           # seed from the real Chrome profile
HUMAN_INPUT = True             # curved mouse, jittered keystrokes

# Files worth carrying over. Caches are skipped: they are large, and a cloned
# cache from a different session is its own fingerprint oddity.
CLONE_ITEMS = [
    "Local State",
    "Default/Preferences",
    "Default/Secure Preferences",
    "Default/Cookies",
    "Default/History",
    "Default/Web Data",
    "Default/Login Data",
    "Default/Favicons",
    "Default/Local Storage",
    "Default/Session Storage",
    "Default/Network",
]
NAV_TIMEOUT_MS = 60_000
STEP_TIMEOUT_MS = 20_000
MAIL_WAIT_S = 180
INBOX_POLL_S = 1.5
INBOX_RELOAD_S = 20
TURNSTILE_WAIT_S = 20
MANUAL_CAPTCHA = os.environ.get("CAPTCHA_MODE", "manual").strip().lower() != "auto"
MANUAL_WAIT_S = int(os.environ.get("CAPTCHA_SECONDS", "300"))   # prompt dwell
CAPTCHA_POLL_S = 1.5

# ------------------------------------------------------------------ solving
# Third-party solve, then inject. The sitekey is public: it ships in the page.
# Token is single-use and bound to sitekey + page host, TTL 300s.
TURNSTILE_SITEKEY = "0x4AAAAAADkESu5VN02iaX-3"
SOLVE_PAGE_URL = SIGNUP_URL

CAPTCHA_API_KEY = os.environ.get("CAPTCHA_API_KEY", "").strip()
CAPTCHA_PROVIDER = (
    os.environ.get("CAPTCHA_PROVIDER", "").strip().lower()
    or ("2captcha" if CAPTCHA_API_KEY else "")
)
SOLVE_TIMEOUT_S = 180          # API wall clock for one token
SOLVE_POLL_S = 5               # provider poll cadence

ENDPOINTS = {
    "2captcha": {
        "create": "https://api.2captcha.com/createTask",
        "result": "https://api.2captcha.com/getTaskResult",
        "task": "TurnstileTaskProxyless",
        "key_field": "clientKey",
    },
    "capsolver": {
        "create": "https://api.capsolver.com/createTask",
        "result": "https://api.capsolver.com/getTaskResult",
        "task": "AntiTurnstileTaskProxyLess",
        "key_field": "clientKey",
    },
}

KEY_RE = re.compile(r"nc_live_[A-Za-z0-9_\-]{12,}")
VERIFY_URL_RE = re.compile(
    r"https://dashboard\.nonecap\.com/verify\?token=[A-Za-z0-9\-._~%]+"
)
# The mail client shows "Verify your NoneCap email". Earlier this was spelled
# "noncap", which matched nothing and burned the whole 180s inbox wait.
SUBJECT_RE = re.compile(r"verif\w*\s+your\s+none?cap\s+email", re.I)
# Timestamp shown in the inbox row, e.g. "11/17/26 12:28:14".
MAIL_DATE_RE = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?"
)


def mail_row_time(text: str) -> Optional[float]:
    """Epoch seconds for the timestamp inside an inbox row, if parseable."""
    m = MAIL_DATE_RE.search(text or "")
    if not m:
        return None
    mon, day, year, hh, mm, ss = m.groups()
    y = int(year)
    if y < 100:
        y += 2000
    try:
        return time.mktime(
            (y, int(mon), int(day), int(hh), int(mm), int(ss or 0), 0, 0, -1)
        )
    except Exception:
        return None

PASSWORD_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_=+"

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


# ------------------------------------------------------------------- helpers


def log(msg: str) -> None:
    print(f"[onboard] {msg}", flush=True)


def make_password(length: int = 20) -> str:
    core = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*-_=+"),
    ]
    core += [secrets.choice(PASSWORD_ALPHABET) for _ in range(length - len(core))]
    secrets.SystemRandom().shuffle(core)
    return "".join(core)


ASSETS_FILE = Path("assets.txt")


def append_asset_key(key: str) -> str:
    """Append `nonecap=` / `nonecap2=` / ... to assets.txt, never rewriting it.

    Existing lines — including keys from other tools — are left untouched. The
    new key goes at the end of the file under the next free slot name.
    """
    text = ""
    try:
        if ASSETS_FILE.exists():
            text = ASSETS_FILE.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""

    highest = 0
    for m in re.finditer(r"^[ \t]*nonecap(\d*)[ \t]*=", text, re.M):
        highest = max(highest, int(m.group(1)) if m.group(1) else 1)

    slot = "nonecap" if highest == 0 else f"nonecap{highest + 1}"
    lead = "" if not text or text.endswith("\n") else "\n"
    with open(ASSETS_FILE, "a", encoding="utf-8") as fh:
        fh.write(f"{lead}{slot}={key}\n")
    return slot


def clone_real_profile(dest: Path) -> bool:
    """Seed our profile from the operator's real Chrome.

    Cookies, history, preferences, local storage — the parts a fresh profile
    lacks and a site can smell. Same user, same machine, so the DPAPI-encrypted
    cookie jar decrypts normally.
    """
    if not REAL_CHROME_PROFILE.exists():
        return False
    if dest.exists() and any(dest.iterdir()):
        return False
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for item in CLONE_ITEMS:
        src = REAL_CHROME_PROFILE / item
        dst = dest / item
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                copied += 1
            elif src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1
        except Exception:
            continue  # a locked cookie db is not fatal
    log(f"  profile cloned: {copied}/{len(CLONE_ITEMS)} items")
    return copied > 0


# Only fix what automation actually breaks. Running real Chrome means the rest
# of the surface — WebGL strings, plugin list, fonts, GPU — is already genuine,
# and patching it would be the tell.
STEALTH_JS = r"""
(() => {
  try {
    if (navigator.webdriver) {
      Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    }
  } catch (e) {}

  try {
    if (!window.chrome) {
      window.chrome = {};
    }
    if (!window.chrome.runtime) {
      window.chrome.runtime = {
        connect: function () {},
        sendMessage: function () {},
        id: undefined,
      };
    }
    if (!window.chrome.loadTimes) {
      window.chrome.loadTimes = function () {
        return { requestTime: 0, startLoadTime: 0, commitLoadTime: 0,
                 finishDocumentLoadTime: 0, finishLoadTime: 0,
                 firstPaintTime: 0, firstPaintAfterLoadTime: 0,
                 navigationType: 'Other', wasFetchedViaSpdy: true,
                 wasNpnNegotiated: true, npnNegotiatedProtocol: 'h2',
                 wasAlternateProtocolAvailable: false,
                 connectionInfo: 'h2' };
      };
    }
    if (!window.chrome.csi) {
      window.chrome.csi = function () {
        return { onloadT: Date.now(), startE: Date.now(), pageT: 0, tran: 15 };
      };
    }
    if (!window.chrome.app) {
      window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: 'disabled', INSTALLED: 'installed',
                        NOT_INSTALLED: 'not_installed' },
        RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run',
                        RUNNING: 'running' },
        getDetails: function () { return null; },
        getIsInstalled: function () { return false; },
      };
    }
  } catch (e) {}

  // Chrome answers 'denied' here while Notification.permission is 'default'.
  try {
    const orig = navigator.permissions.query.bind(navigator.permissions);
    navigator.permissions.query = (params) => {
      if (params && params.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission,
                                 onchange: null });
      }
      return orig(params);
    };
  } catch (e) {}
})();
"""

_MOUSE = {"x": 0, "y": 0}


_CDP_SESSIONS: dict[int, object] = {}


async def install_stealth(context: BrowserContext, page: Optional[Page] = None) -> None:
    """Apply to the main world.

    context.add_init_script lands in Playwright's isolated world for this
    driver, which no page trace can see — and neither can the site's own JS.
    The evaluate below runs in the main world, so that is what actually lands.
    Re-apply after every navigation: a fresh document starts clean.
    """
    try:
        await context.add_init_script(STEALTH_JS)
    except Exception:
        pass
    if page is not None:
        await apply_stealth(page)


async def apply_stealth(page: Page) -> None:
    try:
        await page.evaluate(STEALTH_JS, isolated_context=False)
    except TypeError:
        try:
            await page.evaluate(STEALTH_JS)
        except Exception:
            pass
    except Exception:
        pass


def patch_preferences(dest: Path) -> None:
    """Stop the cloned profile from restoring the operator's own session.

    A clone of the real profile carries 'restore on startup' plus the session
    state, so Chrome reopens their personal tabs inside our automation window.
    """
    import json

    pref = dest / "Default" / "Preferences"
    if not pref.exists():
        return
    try:
        data = json.loads(pref.read_text(encoding="utf-8"))
        data.setdefault("profile", {})["exit_type"] = "Normal"
        data["profile"]["exited_cleanly"] = True
        data.setdefault("session", {})
        data["session"]["restore_on_startup"] = 5      # 5 = open the new tab page
        data["session"].pop("startup_urls", None)
        pref.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    except Exception:
        pass


async def sanitize_context(ctx: BrowserContext, keep: Page) -> None:
    """Close everything the clone restored, and drop target-site cookies.

    The cloned jar can carry a live NoneCap session, which redirects /signup
    straight to the dashboard and leaves no form to fill.
    """
    others = [pg for pg in ctx.pages if pg is not keep]
    for pg in others:
        try:
            await pg.close()
        except Exception:
            pass
    if others:
        log(f"  closed {len(others)} restored tab(s)")
    for domain in ("nonecap.com", ".nonecap.com", "dashboard.nonecap.com"):
        try:
            await ctx.clear_cookies(domain=domain)
        except Exception:
            pass


async def attach_dialogs(page: Page) -> None:
    """Accept whatever the site throws up. A blocked dialog stalls the flow."""
    try:
        page.on(
            "dialog",
            lambda d: asyncio.ensure_future(d.accept()),
        )
    except Exception:
        pass
    try:
        page.on("close", lambda p=page: log(f"  [event] tab closed: {p.url[:70]}"))
    except Exception:
        pass
    try:
        page.on("crash", lambda p=page: log(f"  [event] renderer CRASHED: {p.url[:70]}"))
    except Exception:
        pass


async def live_page(ctx: BrowserContext, current: Page) -> Page:
    """Whatever tab is still alive.

    NoneCap's signup submit sometimes replaces the tab outright. Losing a page
    handle is not the same as losing the browser — pick up a live one and carry
    on with the flow.
    """
    try:
        if not current.is_closed():
            return current
    except Exception:
        pass
    for pg in ctx.pages:
        try:
            if not pg.is_closed() and pg.url and "tempmail.lol" not in pg.url:
                log(f"  tab was replaced — picked up {pg.url[:60]}")
                return pg
        except Exception:
            continue
    for pg in ctx.pages:
        try:
            if not pg.is_closed():
                return pg
        except Exception:
            continue
    log("  no live tab — opening a new one")
    fresh = await ctx.new_page()
    await install_stealth(ctx, fresh)
    await attach_dialogs(fresh)
    return fresh


async def maximize(page: Page) -> None:
    """Left in place for attach-mode debugging; --start-maximized does the job.

    A CDP window-bounds call on a page session is not safe over connect_over_cdp
    — it can take the page target down with it.
    """
    return


def _bezier(p0, p1, p2, t):
    return (
        (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
        (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1],
    )


async def human_move(page: Page, x: float, y: float, steps: int = 12) -> None:
    """Curved path, jittered cadence. Straight-line teleports are the giveaway.

    A renderer stuck behind a Cloudflare challenge can take hundreds of ms per
    mouse event. Once a step is that slow, the path stops being stealthy and
    just costs time, so jump the remaining distance.
    """
    x0, y0 = _MOUSE["x"], _MOUSE["y"]
    cx = x0 + (x - x0) * random.uniform(0.35, 0.65) + random.uniform(-70, 70)
    cy = y0 + (y - y0) * random.uniform(0.35, 0.65) + random.uniform(-70, 70)
    for i in range(1, steps + 1):
        t = i / steps
        t0 = time.monotonic()
        await page.mouse.move(*_bezier((x0, y0), (cx, cy), (x, y), t))
        if time.monotonic() - t0 > 0.15:
            await page.mouse.move(x, y)
            break
        await page.wait_for_timeout(random.randint(7, 24))
    _MOUSE["x"], _MOUSE["y"] = x, y


async def human_click(loc: Locator, page: Page) -> None:
    box = await loc.bounding_box()
    if not box:
        await loc.click()
        return
    tx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    ty = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    await human_move(page, tx, ty)
    await page.wait_for_timeout(random.randint(90, 260))
    await page.mouse.down()
    await page.wait_for_timeout(random.randint(45, 110))
    await page.mouse.up()


async def human_type(page: Page, loc: Locator, text: str) -> None:
    """Click, then real keystrokes with human gaps and the odd correction."""
    started = time.monotonic()
    box = await loc.bounding_box()
    if box:
        await human_move(
            page,
            box["x"] + box["width"] * random.uniform(0.3, 0.7),
            box["y"] + box["height"] * random.uniform(0.4, 0.6),
        )
    await loc.click()
    # A stalled click means the renderer is busy (Cloudflare challenge, heavy
    # page). Typing char by char would then take tens of seconds.
    if time.monotonic() - started > 1.5:
        log("  renderer slow — filling the field")
        await loc.fill(text, timeout=5_000)
        return
    await page.wait_for_timeout(random.randint(60, 160))
    for ch in text:
        t0 = time.monotonic()
        await page.keyboard.type(ch)
        # A flagged IP gets a heavier Cloudflare challenge, and a busy renderer
        # can stall each keystroke for hundreds of ms. Fall back to a single
        # fill rather than sit there typing at 2 characters a second.
        if time.monotonic() - t0 > 0.35 or time.monotonic() - started > 4.0:
            log("  renderer slow — filling the field")
            await loc.fill(text, timeout=5_000)
            return
        gap = random.randint(15, 45)
        if random.random() < 0.05:
            gap += random.randint(60, 140)      # the pause before a tricky char
        await page.wait_for_timeout(gap)
    await page.wait_for_timeout(random.randint(90, 220))


def loc_of(page: Page, cand) -> Locator:
    return cand if isinstance(cand, Locator) else page.locator(cand).first


async def click_first(
    page: Page, candidates: Iterable, label: str, per: int = 6_000
) -> Optional[Locator]:
    for cand in candidates:
        loc = loc_of(page, cand)
        try:
            await loc.wait_for(state="visible", timeout=per)
            await loc.scroll_into_view_if_needed(timeout=2_000)
            if HUMAN_INPUT:
                await human_click(loc, page)
            else:
                await loc.click(timeout=8_000)
            return loc
        except Exception:
            continue
    log(f"  ! no match for {label}")
    return None


async def type_first(
    page: Page, candidates: Iterable, value: str, label: str, per: int = 10_000
) -> Optional[Locator]:
    """Click then key-by-key. Cloudflare scores fill() worse than typing."""
    for cand in candidates:
        loc = loc_of(page, cand)
        try:
            await loc.wait_for(state="visible", timeout=per)
            if HUMAN_INPUT:
                await human_type(page, loc, value)
                # Typing can be cut short by a re-render or a maxlength.
                try:
                    got = await loc.input_value(timeout=2_000)
                except Exception:
                    got = ""
                if got.strip() != value:
                    log(f"  {label} came up short ({len(got)}/{len(value)}) — refilling")
                    await loc.fill(value, timeout=5_000)
            else:
                await loc.fill("", timeout=5_000)
                try:
                    await loc.press_sequentially(value, delay=45, timeout=20_000)
                except AttributeError:
                    await loc.type(value, delay=45, timeout=20_000)
            return loc
        except Exception:
            continue
    log(f"  ! no field for {label}")
    return None


async def reject_cookies(page: Page, wait_s: float = 3.0) -> bool:
    """Click a visible Reject control, polling briefly for it to render.

    The banner hydrates a beat after domcontentloaded, so a single scan can
    miss it. On pages that never show one (login) keep the budget short:
    the caller decides, and the click returns the moment the control exists.
    """
    js = """() => {
        const want = new Set([
            'reject', 'reject all', 'reject cookies', 'decline',
            'decline all', 'essential only', 'necessary only'
        ]);
        for (const el of document.querySelectorAll('button, a, [role="button"], span, div')) {
            const t = (el.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
            if (!want.has(t)) continue;
            const r = el.getBoundingClientRect();
            if (r.width < 4 || r.height < 4) continue;
            const s = getComputedStyle(el);
            if (s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') continue;
            el.click();
            return t;
        }
        return null;
    }"""
    deadline = time.monotonic() + wait_s
    while True:
        try:
            clicked = await page.evaluate(js)
        except Exception:
            clicked = None
        if clicked:
            await page.wait_for_timeout(250)
            log("  cookies rejected")
            return True
        if time.monotonic() >= deadline:
            return False
        await page.wait_for_timeout(250)


async def wait_clear_of_challenge(page: Page, timeout_s: int = 45) -> None:
    """Cloudflare interstitial: just let it settle, a real Chrome usually clears."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        title = ((await page.title()) or "").lower()
        if "just a moment" not in title and "attention required" not in title:
            return
        await page.wait_for_timeout(1_000)
    log("  still behind a challenge — continuing anyway")


async def turnstile_token(page: Page) -> str:
    return await page.evaluate(
        """() => {
            const el = document.querySelector(
                'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
            return el ? (el.value || '') : '';
        }"""
    )


async def _http_json(url: str, payload: dict) -> dict:
    """POST JSON with the stdlib, off the event loop."""
    import json
    import urllib.request

    def _do() -> dict:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    return await asyncio.to_thread(_do)


async def mint_token() -> Optional[str]:
    """Buy a Turnstile token for our sitekey. Provider-agnostic."""
    spec = ENDPOINTS[CAPTCHA_PROVIDER]
    log(f"  minting token via {CAPTCHA_PROVIDER}")

    create = await _http_json(
        spec["create"],
        {
            spec["key_field"]: CAPTCHA_API_KEY,
            "task": {
                "type": spec["task"],
                "websiteURL": SOLVE_PAGE_URL,
                "websiteKey": TURNSTILE_SITEKEY,
            },
        },
    )
    if create.get("errorId", 0) != 0:
        log(f"  createTask rejected: {create.get('errorCode')} "
            f"{create.get('errorDescription', '')}")
        return None
    task_id = create.get("taskId")
    if not task_id:
        log(f"  createTask gave no taskId: {create}")
        return None
    log(f"  task {task_id} queued")

    deadline = time.monotonic() + SOLVE_TIMEOUT_S
    while time.monotonic() < deadline:
        await asyncio.sleep(SOLVE_POLL_S)
        try:
            res = await _http_json(
                spec["result"],
                {spec["key_field"]: CAPTCHA_API_KEY, "taskId": task_id},
            )
        except Exception as exc:
            log(f"  poll error: {exc!r}")
            continue
        if res.get("errorId", 0) != 0:
            log(f"  task failed: {res.get('errorCode')} "
                f"{res.get('errorDescription', '')}")
            return None
        if res.get("status") == "ready":
            token = (res.get("solution") or {}).get("token") or ""
            if token:
                log(f"  token in hand ({len(token)} chars)")
                return token
        log("  still solving…")
    log("  solve timed out")
    return None


async def inject_token(page: Page, token: str) -> bool:
    """Write the token where the widget would have written it.

    The hidden input lives in the light DOM here (checked with the widget dump),
    so a native setter plus input/change is enough. Also fires the callback if
    the page registered one, and a synthetic success isn't needed because the
    form only reads the input's value at submit.
    """
    ok = await page.evaluate(
        """(token) => {
            let el = document.querySelector('input[name="cf-turnstile-response"]');
            if (!el) return false;
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value').set;
            setter.call(el, token);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));

            const host = document.querySelector('.cf-turnstile');
            const cb = host && host.getAttribute('data-callback');
            if (cb && typeof window[cb] === 'function') {
                try { window[cb](token); } catch (e) {}
            }
            return el.value === token;
        }""",
        token,
    )
    await page.wait_for_timeout(500)
    return bool(ok)


async def _click_turnstile(page: Page, timeout_s: int) -> bool:
    """Click the widget's checkbox with the real mouse, then watch the token."""
    boxes = [
        page.locator('div.cf-turnstile iframe').first,
        page.locator('iframe[src*="challenges.cloudflare.com"]').first,
        page.locator('div[class*="turnstile"] iframe').first,
    ]

    frame_el = None
    for cand in boxes:
        try:
            await cand.wait_for(state="visible", timeout=4_000)
            frame_el = cand
            break
        except Exception:
            continue
    if frame_el is None:
        log("  no turnstile widget present")
        return bool(await turnstile_token(page))

    log("  turnstile widget found, clicking")
    deadline = time.monotonic() + timeout_s
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            box = await frame_el.bounding_box()
            if box:
                # Checkbox sits at the left edge, vertically centred.
                await page.mouse.move(
                    box["x"] + 28, box["y"] + box["height"] / 2, steps=12
                )
                await page.wait_for_timeout(250)
                await page.mouse.click(
                    box["x"] + 28, box["y"] + box["height"] / 2
                )
        except Exception as exc:
            log(f"  click failed: {exc!r}")

        for _ in range(10):
            if await turnstile_token(page):
                log(f"  turnstile solved (attempt {attempt})")
                return True
            await page.wait_for_timeout(700)
        log(f"  attempt {attempt} did not take")

    return False


async def solve_turnstile(page: Page, timeout_s: int = TURNSTILE_WAIT_S) -> bool:
    """Click the widget automatically; hand it to the operator only if that fails."""
    await apply_stealth(page)
    if await turnstile_token(page):
        log("  turnstile already passed")
        return True

    if CAPTCHA_API_KEY and CAPTCHA_PROVIDER in ENDPOINTS:
        token = await mint_token()
        if token:
            if await inject_token(page, token):
                log("  token injected")
                return True
            log("  injection target missing")
        log("  API solve failed — falling back to a click")

    if await _click_turnstile(page, timeout_s):
        return True

    if MANUAL_CAPTCHA:
        # Window can be closed by hand mid-wait — report it, don't traceback.
        try:
            await page.bring_to_front()
            await page.locator('div.cf-turnstile').first.scroll_into_view_if_needed(
                timeout=3_000
            )
        except Exception:
            pass
        print()
        print("  " + "-" * 60)
        print("  AUTO-CLICK DIDN'T TAKE. CLICK THE 'Verify you are human' BOX.")
        print(f"  Waiting up to {MANUAL_WAIT_S}s for the token...")
        print("  " + "-" * 60)
        deadline = time.monotonic() + MANUAL_WAIT_S
        while time.monotonic() < deadline:
            try:
                if await turnstile_token(page):
                    log("  turnstile solved (manual)")
                    await page.wait_for_timeout(600)
                    return True
                await page.wait_for_timeout(int(CAPTCHA_POLL_S * 1000))
            except Exception as exc:
                log(f"  page died while waiting: {type(exc).__name__}")
                if _CHROME_PROC is not None:
                    log(f"  chrome exit code: {_CHROME_PROC.poll()}")
                try:
                    live = [p for p in page.context.pages if not p.is_closed()]
                    log(f"  tabs still open: {len(live)}")
                except Exception:
                    pass
                return False
        log("  manual captcha timed out")
        return False

    log("  turnstile unsolved")
    return False


async def rotate_address(page: Page, current: str, timeout_s: int = 10) -> str:
    """Hit Reset and wait for a different address.

    The site pins the address per browser session, so a persistent profile hands
    back the same one every run — and a reused address is a reused footprint.
    """
    # Direct DOM click first: the temp-mail page's scripts can stall the input
    # pipeline, and the mouse path has no stealth value on that site.
    hit: Optional[bool] = None
    for label in ("Reset", "Change"):
        if await click_text_js(page, label):
            log(f"  clicked '{label}'")
            hit = True
            break
    if hit is None:
        for label in ("Reset", "Change"):
            if await click_text_exact(page, label):
                log(f"  clicked '{label}' by rect")
                hit = True
                break
    if hit is None:
        hit = await click_first(
            page,
            [
                page.get_by_role(
                    "button", name=re.compile(r"^\s*(reset|change email|new address)\s*$", re.I)
                ),
                'button:has-text("Reset")',
                page.get_by_text(re.compile(r"^\s*Reset\s*$", re.I)).first,
                page.get_by_text(re.compile(r"^\s*Change\s*$", re.I)).first,
            ],
            "temp mail reset",
            per=2_000,
        )
    if hit is None:
        return current

    log("  address reset, waiting for a new one")
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    rect_retry = False
    while time.monotonic() < deadline:
        try:
            found = await page.evaluate(
                r"""() => {
                    const re = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
                    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    const out = new Set();
                    let n;
                    while ((n = walk.nextNode())) {
                        const t = (n.textContent || '').trim();
                        if (!t || t.length > 96) continue;
                        const m = t.match(re);
                        if (m) out.add(m[0]);
                    }
                    return [...out].filter(a => !/tempmail|temp-mail|example\.com/i.test(a));
                }"""
            )
        except Exception:
            found = []
        fresh = [a for a in found if a != current]
        if fresh:
            new = sorted(fresh, key=len)[0]
            log(f"  new address: {new}")
            return new
        if not rect_retry and time.monotonic() - started > 2.5:
            rect_retry = True
            if await click_text_exact(page, "Reset"):
                log("  retried reset by rect")
        await page.wait_for_timeout(600)
    log("  reset gave no new address — keeping the old one")
    return current


async def click_text_js(page: Page, text: str) -> bool:
    """Click an element whose trimmed text matches, without mouse round trips.

    Used on the temp-mail utility page: a busy renderer there can queue mouse
    events for seconds, and nothing about that click needs to look human.
    """
    try:
        return bool(
            await page.evaluate(
                """(want) => {
                    for (const el of document.querySelectorAll(
                            'button, a, [role="button"], span, div')) {
                        if ((el.textContent || '').trim() !== want) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 4 || r.height < 4) continue;
                        el.click();
                        return true;
                    }
                    return false;
                }""",
                text,
            )
        )
    except Exception:
        return False


async def click_text_exact(page: Page, text: str) -> bool:
    """Click the visible element whose trimmed text is exactly `text`.

    The temp-mail controls are not buttons with accessible names, so role
    queries miss them. Find the deepest matching node, take its rect, click it.
    """
    box = await page.evaluate(
        """(want) => {
            const hits = [];
            for (const el of document.querySelectorAll('button, a, div, span, p')) {
                const t = (el.textContent || '').trim();
                if (t !== want) continue;
                const r = el.getBoundingClientRect();
                if (r.width < 4 || r.height < 4) continue;
                if (r.top < 0 || r.left < 0) continue;
                hits.push({x: r.x, y: r.y, w: r.width, h: r.height,
                           depth: (el.compareDocumentPosition(document.body) ? 0 : 0)});
            }
            if (!hits.length) return null;
            hits.sort((a, b) => a.w * a.h - b.w * b.h);
            return hits[0];
        }""",
        text,
    )
    if not box:
        return False
    await human_move(
        page, box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    )
    await page.wait_for_timeout(random.randint(80, 200))
    await page.mouse.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
    return True


async def read_temp_address(page: Page, tries: int = 30) -> str:
    js = r"""() => {
        const re = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/;
        const out = new Set();
        const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        let n;
        while ((n = walk.nextNode())) {
            const t = (n.textContent || '').trim();
            if (!t || t.length > 96) continue;
            const m = t.match(re);
            if (m) out.add(m[0]);
        }
        return [...out];
    }"""
    for _ in range(tries):
        found = await page.evaluate(js)
        usable = [
            a
            for a in found
            if not re.search(r"(tempmail|temp-mail|example\.com)", a, re.I)
        ]
        if usable:
            return sorted(usable, key=len)[0]
        await page.wait_for_timeout(700)
    raise RuntimeError("temp mail address never rendered")


INBOX_ROWS_JS = r"""(src) => {
    const re = new RegExp(src, 'i');
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll('td, th, a, div, span, p, tr')) {
        const t = (el.innerText || el.textContent || '').trim();
        if (!t || t.length > 200 || !re.test(t)) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 4 || r.height < 4) continue;
        // A mail is only identifiable by subject + timestamp, so walk up to
        // the first small ancestor that carries a date/time. Stop before the
        // container: its text includes the live email counter and would make
        // every old row look new.
        let node = el, key = t;
        for (let i = 0; i < 8 && node; i++, node = node.parentElement) {
            const it = (node.innerText || '').replace(/\s+/g, ' ').trim();
            if (it.length > 200) break;
            if (/\d{1,2}\/\d{1,2}\/\d{2,4}/.test(it) || /\d{1,2}:\d{2}/.test(it)) {
                key = it;
                break;
            }
        }
        if (seen.has(key)) continue;
        seen.add(key);
        out.push({key, text: t, x: r.x, y: r.y, w: r.width, h: r.height});
    }
    return out;
}"""


async def inbox_rows(page: Page) -> list[dict]:
    """Every NoneCap-subject row currently on the TempMail page.

    Keyed by subject + timestamp so a mail that was already sitting there
    before signup can be told apart from the one this run just caused.
    """
    try:
        return await page.evaluate(INBOX_ROWS_JS, SUBJECT_RE.pattern) or []
    except Exception:
        return []


async def wait_for_verification_row(
    page: Page,
    known: set[str],
    not_before: float,
    settle_s: float = 3.0,
    timeout_s: int = MAIL_WAIT_S,
) -> Page:
    """Wait for a mail that was NOT in the inbox before signup, then open it.

    Clicking a pre-existing verification mail is worse than waiting: its token
    belongs to an older account, so the dashboard would verify the wrong
    address. Give the server a few seconds to deliver, then only accept a row
    that is either absent from `known` or carries a timestamp from this run.
    """
    before = list(page.context.pages)
    try:
        if "tempmail" not in (page.url or ""):
            await page.goto(MAIL_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            log("  inbox reopened")
    except Exception:
        pass
    await page.wait_for_timeout(int(settle_s * 1000))
    deadline = time.monotonic() + timeout_s
    last_reload = time.monotonic()
    while time.monotonic() < deadline:
        rows = await inbox_rows(page)
        fresh = [r for r in rows if r["key"] not in known]
        if not fresh:
            # Key matching can miss if the list re-renders differently; fall
            # back to the visible timestamp. Old mail is still excluded.
            fresh = [
                r
                for r in rows
                if (ts := mail_row_time(r["key"])) and ts >= not_before - 90
            ]
        if fresh:
            # The subject cell is the smallest matching node for that mail.
            fresh.sort(key=lambda r: (r["w"] * r["h"], r["y"]))
            row = fresh[0]
            label = re.sub(r"\s+", " ", row["text"]).strip()
            log(f"  new mail: {label}")
            x = row["x"] + min(row["w"] * 0.45, 220)
            y = row["y"] + row["h"] / 2
            await human_move(page, x, y)
            await page.wait_for_timeout(random.randint(80, 200))
            await page.mouse.click(x, y)
            await page.wait_for_timeout(900)
            for pg in page.context.pages:
                if pg not in before and not pg.is_closed():
                    log("  mail opened in a new tab")
                    return pg
            return page
        if time.monotonic() - last_reload > INBOX_RELOAD_S:
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30_000)
            except Exception:
                pass
            last_reload = time.monotonic()
        await page.wait_for_timeout(INBOX_POLL_S * 1000)
    shot = "inbox-timeout.png"
    try:
        await page.screenshot(path=shot)
        log(f"  inbox screenshot: {shot}")
    except Exception:
        pass
    raise RuntimeError(f"no NEW verification email after {timeout_s}s")


def frames_of(page: Page) -> list[Frame]:
    return list(page.frames)


async def wait_create_outcome(page: Page, timeout_s: float = 30) -> None:
    """Confirm the signup, or fail fast with the site's own reason.

    NoneCap rate-limits by IP ("Too many sign-ups from your network"). That
    banner is immediate, so surfacing it beats a 30s wait for a success state
    that will never arrive.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        text = await page.evaluate("() => document.body.innerText") or ""
        if re.search(r"check your email", text, re.I):
            log("  account created — mail sent")
            return
        m = re.search(
            r"(too many sign-?ups[^\n]*|already (?:registered|exists|in use)[^\n]*"
            r"|please try again later[^\n]*|invalid[^\n]*)",
            text,
            re.I,
        )
        if m:
            raise RuntimeError(f"signup rejected: {m.group(0).strip()[:160]}")
        await page.wait_for_timeout(300)
    raise RuntimeError("create account never confirmed")


async def find_verify_url(page: Page) -> Optional[str]:
    """Absolute verify URL from any frame: anchors first, then raw HTML.

    The mail body lives in an iframe, and the button is an anchor, so the href
    is available without clicking anything.
    """
    for frame in frames_of(page):
        try:
            href = await frame.evaluate(
                """(src) => {
                    const re = new RegExp(src);
                    for (const a of document.querySelectorAll('a[href]')) {
                        if (re.test(a.href)) return a.href;
                    }
                    return null;
                }""",
                VERIFY_URL_RE.pattern,
            )
        except Exception:
            href = None
        if href:
            return href
    for frame in frames_of(page):
        try:
            html = await frame.content()
        except Exception:
            continue
        m = VERIFY_URL_RE.search(html)
        if m:
            return m.group(0)
    return None


async def click_verify_email(page: Page, timeout_s: int = 25) -> Page:
    """Follow the mail's verify control and return the page showing the result.

    The href is preferred: the button often has target=_blank, and a popup is a
    tab nothing else in the flow knows about. Navigating the mail tab itself to
    the token URL keeps "Email Verified" and its "Go to sign in" button in a tab
    we already hold.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        url = await find_verify_url(page)
        if url:
            log("  verify link found in the mail")
            await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            return page

        for frame in frames_of(page):
            for role in ("button", "link"):
                for label in (
                    r"verify email",
                    r"verify my email",
                    r"confirm email",
                    r"verify",
                ):
                    try:
                        ctrl = frame.get_by_role(
                            role, name=re.compile(label, re.I)
                        ).first
                        if not await ctrl.is_visible(timeout=600):
                            continue
                    except Exception:
                        continue
                    log(f"  clicking '{label}' in the mail")
                    before = list(page.context.pages)
                    try:
                        await ctrl.click(timeout=6_000)
                    except Exception:
                        continue
                    await page.wait_for_timeout(1_500)
                    for pg in page.context.pages:
                        if pg not in before and not pg.is_closed():
                            log("  verify opened a new tab")
                            return pg
                    return page
        await page.wait_for_timeout(400)
    raise RuntimeError("no verify control and no token URL in the mail")


async def wait_email_verified(page: Page, timeout_ms: int = 15_000) -> None:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for frame in frames_of(page):
            try:
                await frame.get_by_text(
                    re.compile(r"email verified", re.I)
                ).first.wait_for(state="visible", timeout=800)
                log("  email verified")
                return
            except Exception:
                continue
        await page.wait_for_timeout(400)
    log("  no 'email verified' banner — continuing anyway")


async def go_to_signin(page: Page) -> Page:
    """Click the in-mail 'Go to sign in' button and return the page to use.

    That button can navigate in place or open a tab. Either way the flow needs
    the page that ends up holding the login form, not the tab it started on.
    The caller navigates to /login if this lands somewhere else.
    """
    before = list(page.context.pages)
    for frame in frames_of(page):
        try:
            btn = frame.get_by_text(re.compile(r"go to sign ?in", re.I)).first
            await btn.wait_for(state="visible", timeout=1_200)
            await btn.click(timeout=4_000)
            log("  go-to-signin clicked")
            break
        except Exception:
            continue

    await page.wait_for_timeout(500)
    for pg in page.context.pages:
        if pg not in before and not pg.is_closed():
            log("  go-to-signin opened a new tab")
            return pg
    return page


async def sign_in(page: Page, email: str, password: str) -> None:
    await reject_cookies(page, wait_s=1.0)
    await type_first(
        page,
        [
            'input[type="email"]',
            'input[placeholder*="example.com"]',
            'input[name="email"]',
        ],
        email,
        "login email",
    )
    await type_first(
        page,
        ['input[type="password"]', 'input[name="password"]'],
        password,
        "login password",
    )
    await click_first(
        page,
        [
            page.get_by_role("button", name=re.compile(r"^\s*sign in\s*$", re.I)),
            'button[type="submit"]',
        ],
        "sign in",
    )
    try:
        await page.wait_for_url(
            re.compile(r"dashboard\.nonecap\.com/(?!login)"), timeout=45_000
        )
    except PWTimeout:
        raise RuntimeError("sign-in did not reach the dashboard")
    log("  signed in")


async def open_api_keys(page: Page) -> None:
    hit = await click_first(
        page,
        [
            page.get_by_role("link", name=re.compile(r"api keys", re.I)),
            page.get_by_text(re.compile(r"^\s*API keys\s*$", re.I)),
            'a[href*="api-keys"]',
        ],
        "api keys nav",
        per=5_000,
    )
    if hit is None:
        await page.goto(
            "https://dashboard.nonecap.com/api-keys",
            wait_until="domcontentloaded",
            timeout=NAV_TIMEOUT_MS,
        )
    try:
        await page.get_by_text(
            re.compile(r"bearer tokens for", re.I)
        ).first.wait_for(state="visible", timeout=15_000)
    except PWTimeout:
        pass
    log("  api keys page")


async def wait_for_key(page: Page, timeout_ms: int = 20_000) -> Optional[str]:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            text = await page.evaluate(
                """() => {
                    const parts = [];
                    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    let n;
                    while ((n = walk.nextNode())) {
                        const t = n.textContent || '';
                        if (t.includes('nc_live_')) parts.push(t);
                    }
                    for (const el of document.querySelectorAll('input, textarea, code, pre')) {
                        if (el.value && el.value.includes('nc_live_')) parts.push(el.value);
                        if (el.textContent && el.textContent.includes('nc_live_')) parts.push(el.textContent);
                    }
                    return parts.join('\\n');
                }"""
            )
            m = KEY_RE.search(text or "")
            if m:
                return m.group(0)
        except Exception:
            pass
        await page.wait_for_timeout(500)
    return None


async def clipboard_key(page: Page) -> Optional[str]:
    """Read the key back from the clipboard after the page's copy button.

    The key list masks the value and only offers an eye/copy control. Copying
    and reading the clipboard is a shorter path than guessing which icon
    unmasks it.
    """
    try:
        await page.context.grant_permissions(
            ["clipboard-read", "clipboard-write"]
        )
    except Exception:
        pass
    try:
        await page.bring_to_front()
        text = await page.evaluate("() => navigator.clipboard.readText()")
    except Exception:
        return None
    m = KEY_RE.search(text or "")
    return m.group(0) if m else None


async def reveal_key_on_page(page: Page) -> Optional[str]:
    """Reveal a key that is already listed, using its Show key button.

    Signup provisions a `default` key, so minting another one just leaves
    clutter behind. Rows expose the value only after the eye button is clicked.
    """
    buttons = page.locator('button[aria-label="Show key" i]')
    try:
        count = await buttons.count()
    except Exception:
        return None
    if count == 0:
        return await wait_for_key(page, timeout_ms=1_500)

    indices = list(range(count))
    try:
        preferred = await page.evaluate(
            """() => {
                const btns = [...document.querySelectorAll('button[aria-label]')]
                    .filter(b => /show key/i.test(b.getAttribute('aria-label') || ''));
                const out = [];
                btns.forEach((b, i) => {
                    let node = b.parentElement, txt = '';
                    for (let k = 0; k < 4 && node; k++, node = node.parentElement) {
                        txt = node.innerText || '';
                        if (txt.length > 40) break;
                    }
                    if (/\\bdefault\\b/i.test(txt)) out.push(i);
                });
                return out;
            }"""
        )
        if preferred:
            indices = preferred + [i for i in indices if i not in preferred]
    except Exception:
        pass

    for i in indices:
        btn = buttons.nth(i)
        try:
            if not await btn.is_visible():
                continue
            await btn.click(timeout=3_000)
        except Exception:
            continue
        await page.wait_for_timeout(300)
        key = await wait_for_key(page, timeout_ms=2_500)
        if key:
            log("  revealed an existing key")
            return key
    return None


async def create_key(page: Page) -> str:
    # Reuse the provisioned key before creating anything new.
    existing = await reveal_key_on_page(page)
    if existing:
        return existing
    log("  no key listed — minting one")
    await click_first(
        page,
        [
            page.get_by_role("button", name=re.compile(r"new key", re.I)),
            'button:has-text("New key")',
            'button:has-text("+ New key")',
        ],
        "new key button",
        per=10_000,
    )
    await page.wait_for_timeout(400)

    dialog = page.locator('[role="dialog"]').first
    try:
        if await dialog.is_visible(timeout=2_000):
            for sel in ['input[type="text"]', 'input[name="name"]']:
                field = dialog.locator(sel).first
                if await field.is_visible(timeout=1_000):
                    try:
                        await field.fill(f"bench-{int(time.time())}")
                    except Exception:
                        pass
                    break
            for label in (r"create key", r"create", r"generate", r"confirm"):
                try:
                    btn = dialog.get_by_role(
                        "button", name=re.compile(label, re.I)
                    ).first
                    await btn.wait_for(state="visible", timeout=2_000)
                    await btn.click(timeout=6_000)
                    break
                except Exception:
                    continue
    except Exception:
        pass

    key = await wait_for_key(page)
    if key:
        return key

    log("  masked everywhere — hunting the eyeball")
    for _ in range(6):
        rows = page.locator(
            'tr:has-text("nc_"), li:has-text("nc_"), div:has-text("nc_")'
        )
        total = await rows.count()
        for r in range(min(total, 6)):
            buttons = rows.nth(r).locator("button")
            n = await buttons.count()
            for i in range(n):
                btn = buttons.nth(i)
                labels = []
                for attr in ("aria-label", "title"):
                    try:
                        labels.append(await btn.get_attribute(attr) or "")
                    except Exception:
                        pass
                tag = " ".join(labels).lower()
                if any(w in tag for w in ("delete", "remove", "revoke", "trash")):
                    continue
                try:
                    await btn.click(timeout=2_000)
                    await page.wait_for_timeout(200)
                except Exception:
                    continue
        key = await wait_for_key(page, timeout_ms=6_000)
        if key:
            return key
        # Masked by design: copy it, then read the clipboard back.
        for sel in (
            'button:has-text("Copy")',
            'button[aria-label*="copy" i]',
            'button[title*="copy" i]',
        ):
            try:
                btn = page.locator(sel).first
                if not await btn.is_visible(timeout=700):
                    continue
                await btn.click(timeout=2_000)
                ck = await clipboard_key(page)
                if ck:
                    log("  key read from clipboard")
                    return ck
            except Exception:
                continue
        await page.wait_for_timeout(700)

    raise RuntimeError("key never appeared in the DOM")


# --------------------------------------------------------------------- steps


def resolve_chrome() -> Optional[str]:
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    return None


_LOCK = PROFILE_DIR.with_suffix(".lock")


def acquire_lock() -> bool:
    """Refuse to start a second run. Two runs reap each other's Chrome."""
    try:
        if _LOCK.exists():
            old = int(_LOCK.read_text().strip() or 0)
            if old:
                import subprocess as sp

                alive = sp.run(
                    ["tasklist", "/FI", f"PID eq {old}", "/NH"],
                    capture_output=True,
                    text=True,
                ).stdout
                if str(old) in alive:
                    log(f"another run is live (pid {old}) — refusing to start")
                    return False
        _LOCK.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return True


def release_lock() -> None:
    try:
        _LOCK.unlink(missing_ok=True)
    except Exception:
        pass


def reap_stale_chromes() -> int:
    """Kill only Chromes running out of our own profile dirs.

    A crashed run leaves the profile locked, which then forces the launch onto
    a timestamped sibling profile and orphans the previous one. Scoped by
    command line so a personal Chrome is never touched.
    """
    killed = 0
    try:
        import subprocess

        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                "Where-Object { $_.CommandLine -like '*nonecap-chrome-profile*' } | "
                "Select-Object -ExpandProperty ProcessId",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
        for line in out.split():
            if line.strip().isdigit():
                subprocess.run(
                    ["taskkill", "/PID", line.strip(), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
                killed += 1
    except Exception:
        pass
    if killed:
        log(f"  reaped {killed} stale chrome process(es)")
    return killed


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_CHROME_PROC: Optional[subprocess.Popen] = None


def _chrome_ready(port: int, timeout_s: float = 25.0) -> bool:
    import json as _json
    import urllib.request

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2
            ) as r:
                if _json.loads(r.read().decode()).get("webSocketDebuggerUrl"):
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


async def launch(p, headless: bool) -> BrowserContext:
    """Start Chrome ourselves, then attach over CDP.

    Driver-launched Chrome carries --disable-blink-features=AutomationControlled,
    which Chrome prints in its own warning bar. Any flag that produces that bar
    is a fingerprint you handed over for free. A plain chrome.exe with only
    --remote-debugging-port shows nothing, and patchright's protocol-level
    patches still apply because we attach rather than launch.
    """
    global _CHROME_PROC

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    if CLONE_PROFILE:
        clone_real_profile(PROFILE_DIR)
        patch_preferences(PROFILE_DIR)

    exe = resolve_chrome()
    if not exe:
        raise RuntimeError("no chrome.exe found in the usual locations")

    port = _free_port()
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--start-maximized",
        "about:blank",          # never boot on chrome://newtab in an attached profile
    ]
    if headless:
        args.insert(1, "--headless=new")

    log(f"  chrome on :{port} (no automation flags)")
    chrome_log = PROFILE_DIR / "chrome-stderr.log"
    _stderr = open(chrome_log, "w", encoding="utf-8", errors="ignore")
    _CHROME_PROC = subprocess.Popen(
        args, stdout=_stderr, stderr=subprocess.STDOUT
    )
    if not _chrome_ready(port, timeout_s=8):
        # A previous run may still hold this profile. Chrome then forwards to
        # that instance and never opens our port, so reap and try once more.
        log("  port did not open — reaping stale chrome and retrying")
        kill_chrome()
        reap_stale_chromes()
        _CHROME_PROC = subprocess.Popen(
            args, stdout=_stderr, stderr=subprocess.STDOUT
        )
        if not _chrome_ready(port, timeout_s=20):
            raise RuntimeError("chrome never opened its debugging port")

    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
    ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
    return ctx


PROBE_JS = r"""async () => {
    const gl = document.createElement('canvas').getContext('webgl');
    const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
    let notif = 'n/a';
    try {
        notif = (await navigator.permissions.query({name: 'notifications'})).state;
    } catch (e) {}
    return {
        webdriver: navigator.webdriver,
        plugins: navigator.plugins.length,
        mimeTypes: navigator.mimeTypes.length,
        chromeKeys: window.chrome ? Object.keys(window.chrome) : null,
        chromeApp: !!(window.chrome && window.chrome.app),
        stealthMarker: window.chrome && window.chrome.app ? 1 : 0,
        ua: navigator.userAgent,
        uaData: navigator.userAgentData ? navigator.userAgentData.platform : null,
        languages: navigator.languages,
        platform: navigator.platform,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory: navigator.deviceMemory,
        webglVendor: dbg ? gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL) : null,
        webglRenderer: dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : null,
        notifPermission: notif,
        screen: [screen.width, screen.height, screen.colorDepth],
        window: [window.outerWidth, window.outerHeight],
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        touchPoints: navigator.maxTouchPoints,
    };
}"""


def kill_chrome() -> None:
    global _CHROME_PROC
    if _CHROME_PROC and _CHROME_PROC.poll() is None:
        try:
            _CHROME_PROC.terminate()
            _CHROME_PROC.wait(timeout=8)
        except Exception:
            try:
                _CHROME_PROC.kill()
            except Exception:
                pass
    _CHROME_PROC = None


async def probe(headless: bool) -> None:
    import json

    async with async_playwright() as p:
        ctx = await launch(p, headless)
        page = await ctx.new_page()
        await install_stealth(ctx, page)
        await maximize(page)
        await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        await page.wait_for_timeout(2_000)
        data = await page.evaluate(PROBE_JS, isolated_context=False)
        print(json.dumps(data, indent=2))
        await ctx.close()
        kill_chrome()


async def run(headless: bool, keep_open: bool) -> None:
    email = ""
    password = make_password()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await launch(p, headless)
        page = context.pages[0] if context.pages else await context.new_page()
        await install_stealth(context, page)
        await sanitize_context(context, page)
        await attach_dialogs(page)
        await maximize(page)

        try:
            log("signup page")
            await page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await apply_stealth(page)
            await wait_clear_of_challenge(page)
            await reject_cookies(page)

            log("temp mail")
            mail = await context.new_page()
            await install_stealth(context, mail)
            await attach_dialogs(mail)
            await mail.goto(MAIL_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            email = await read_temp_address(mail)
            log(f"  address: {email}")
            email = await rotate_address(mail, email)
            # Remember what is already in this inbox: anything visible from
            # here on is a leftover from an earlier run, not this signup.
            known_mail = {r["key"] for r in await inbox_rows(mail)}
            log(f"  inbox baseline: {len(known_mail)} existing mail(s) ignored")
            # Park the mail tab. TempMail's ad scripts keep the CPU busy, and
            # that stalls typing and the Turnstile check on the signup tab.
            try:
                await mail.goto("about:blank", timeout=15_000)
            except Exception:
                pass

            await page.bring_to_front()
            await type_first(
                page,
                [
                    'input[type="email"]',
                    'input[placeholder*="example.com"]',
                    'input[name="email"]',
                ],
                email,
                "signup email",
            )
            await type_first(
                page,
                ['input[type="password"]', 'input[placeholder*="8 characters" i]'],
                password,
                "signup password",
            )
            log(f"  password: {password}")

            await solve_turnstile(page)
            try:
                passed = await turnstile_token(page)
            except Exception:
                log("  browser closed after the captcha prompt — exiting")
                return
            if not passed:
                raise RuntimeError("captcha not passed — token still empty")

            terms = page.locator('input[type="checkbox"]').first
            try:
                if not await terms.is_checked():
                    await terms.check(force=True, timeout=5_000)
            except Exception:
                await click_first(
                    page,
                    [page.get_by_text(re.compile(r"i agree to the", re.I)).first],
                    "terms checkbox",
                    per=4_000,
                )
            log("  terms accepted")

            signup_ts = time.time()
            await click_first(
                page,
                [
                    page.get_by_role(
                        "button", name=re.compile(r"create account", re.I)
                    ),
                    'button[type="submit"]',
                ],
                "create account",
            )
            try:
                await wait_create_outcome(page)
            except Exception as exc:
                if isinstance(exc, RuntimeError):
                    raise
                # The submit can replace the tab. Mail still goes out.
                log(f"  tab died after submit ({type(exc).__name__}) — continuing")
                page = await live_page(context, page)

            await mail.bring_to_front()
            mail = await wait_for_verification_row(mail, known_mail, signup_ts)
            mail = await click_verify_email(mail)
            await mail.bring_to_front()
            await wait_email_verified(mail)

            page = await go_to_signin(mail)
            page = await live_page(context, page)
            await page.bring_to_front()
            if "/login" not in page.url:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            await wait_clear_of_challenge(page)

            log("login")
            await sign_in(page, email, password)

            await open_api_keys(page)
            log("reading key")
            key = await create_key(page)

            print()
            print("=" * 68)
            print(f"  email    : {email}")
            print(f"  password : {password}")
            print(f"  api key  : {key}")
            print("=" * 68)
            print()

            with open("nonecap_accounts.txt", "a", encoding="utf-8") as fh:
                fh.write(f"{email}\t{password}\t{key}\n")
            log("appended to nonecap_accounts.txt")

            try:
                slot = append_asset_key(key)
                log(f"appended to assets.txt as {slot}=")
            except Exception as exc:
                log(f"assets.txt write failed: {type(exc).__name__}: {exc}")

        except Exception as exc:
            if "Target page, context or browser has been closed" in str(exc):
                log("browser window was closed — stopping clean")
                if _CHROME_PROC is not None:
                    log(f"  chrome exit code: {_CHROME_PROC.poll()}")
                try:
                    tail = (PROFILE_DIR / "chrome-stderr.log").read_text(
                        encoding="utf-8", errors="ignore"
                    ).strip().splitlines()[-6:]
                    for line in tail:
                        log(f"  chrome: {line}")
                except Exception:
                    pass
                return
            shot = f"fail-{int(time.time())}.png"
            try:
                await page.screenshot(path=shot, full_page=True)
                log(f"failure screenshot: {shot}")
            except Exception:
                pass
            traceback.print_exc()
            if not headless and not keep_open and sys.stdin and sys.stdin.isatty():
                log("holding the window so you can read the widget state")
                log("press enter here to close it")
                try:
                    await asyncio.to_thread(input)
                except (EOFError, KeyboardInterrupt):
                    pass
            raise

        if keep_open:
            log("holding the browser open — ctrl+c to stop")
            await asyncio.Event().wait()

        await context.close()
        kill_chrome()


def main() -> None:
    ap = argparse.ArgumentParser(description="NoneCap signup -> API key (Chrome)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--keep-open", action="store_true")
    ap.add_argument(
        "--fresh", action="store_true", help="wipe the chrome profile first"
    )
    ap.add_argument(
        "--probe", action="store_true", help="print the browser fingerprint and exit"
    )
    args = ap.parse_args()
    if args.probe:
        asyncio.run(probe(args.headless))
        return
    if not acquire_lock():
        return
    try:
        if args.fresh:
            shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            print(f"[onboard] profile wiped: {PROFILE_DIR}", flush=True)
        asyncio.run(run(headless=args.headless, keep_open=args.keep_open))
    finally:
        kill_chrome()
        release_lock()


if __name__ == "__main__":
    main()
