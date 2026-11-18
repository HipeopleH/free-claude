import asyncio, json, os, subprocess, time, socket, urllib.request
from patchright.async_api import async_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PROFILE = os.path.join(os.environ["LOCALAPPDATA"], "nonecap-cdp-probe")

def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); return s.getsockname()[1]

async def main():
    port = free_port()
    proc = subprocess.Popen(
        [CHROME, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE}",
         "--no-first-run", "--no-default-browser-check"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    print("pid", proc.pid, "port", port)
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
                print("version:", json.loads(r.read().decode()).get("Browser"))
                break
        except Exception:
            time.sleep(0.25)
    else:
        print("never came up; chrome said:", proc.stdout.read().decode(errors="ignore")[:800])
        return

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=3) as r:
        print("targets:", [(t["type"], t["url"][:40]) for t in json.loads(r.read().decode())])

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        print("contexts:", len(browser.contexts))
        ctx = browser.contexts[0]
        print("pages:", [(pg.url, pg.is_closed()) for pg in ctx.pages])
        page = await ctx.new_page()
        print("new page ok", page.url)
        await page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)
        print("title:", await page.title())
        await page.screenshot(path="cdp-check.png")
        print("alive?", not page.is_closed())
        await browser.close()
    print("proc alive after browser.close:", proc.poll() is None)
    try: proc.terminate()
    except Exception: pass

asyncio.run(main())
