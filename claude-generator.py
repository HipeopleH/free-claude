# language: python
# module: ms_account_creator.py
# runtime: python 3.10+, playwright 1.40+
# dependency: playwright (install via `pip install playwright` then `playwright install chromium`)

import asyncio
import json
import random
import re
import string
import subprocess
import sys
import time

# ==========================================
# AUTOMATIC DEPENDENCY CHECK & INSTALLER
# ==========================================
def ensure_dependencies():
    print("[+] Checking environment dependencies...")
    
    # 1. Check & Install 'playwright' python package
    try:
        import playwright
    except ImportError:
        print("[!] 'playwright' package is not installed.")
        choice = input("[?] Would you like to install it automatically now? (y/n): ").strip().lower()
        if choice == 'y':
            print("[+] Installing playwright via pip...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright"])
            import playwright
        else:
            print("[-] Playwright package is required to run this script. Exiting.")
            sys.exit(1)

    # 2. Check & Install Chromium browser binary for Playwright
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # Try launching chromium headlessly to verify browser binaries are present
            try:
                browser = p.chromium.launch(headless=True)
                browser.close()
            except Exception:
                raise FileNotFoundError("Chromium binary not found.")
    except Exception:
        print("[!] Playwright Chromium browser binary is missing or not fully installed.")
        choice = input("[?] Would you like to automatically download/install Chromium now? (y/n): ").strip().lower()
        if choice == 'y':
            print("[+] Downloading and installing Chromium browser binaries via playwright...")
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        else:
            print("[-] Chromium browser is required to run automation. Exiting.")
            sys.exit(1)

# Run dependency check before anything else
ensure_dependencies()

MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]

FIRST_NAMES = [
    "Liam", "Noah", "Oliver", "Elijah", "James", "William", "Benjamin", "Lucas", "Henry", "Alexander",
    "Mason", "Michael", "Ethan", "Daniel", "Logan", "Jackson", "Levi", "Sebastian", "Mateo", "Jack",
    "Owen", "Theodore", "Aiden", "Samuel", "Joseph", "John", "David", "Wyatt", "Matthew", "Luke"
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Miller", "Davis", "Garcia", "Rodriguez", "Wilson",
    "Martinez", "Anderson", "Taylor", "Thomas", "Hernandez", "Moore", "Martin", "Jackson", "Thompson", "White",
    "Lopez", "Lee", "Gonzalez", "Harris", "Clark", "Lewis", "Robinson", "Walker", "Perez", "Hall"
]


def generate_random_string(length: int = 10) -> str:
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def generate_random_password(length: int = 12) -> str:
    """Generate a fully random, robust password with letters, digits, and symbols."""
    lower = string.ascii_lowercase
    upper = string.ascii_uppercase
    digits = string.digits
    symbols = "!@#$%^&*()"
    
    chars = [
        random.choice(lower),
        random.choice(upper),
        random.choice(digits),
        random.choice(symbols)
    ]
    
    all_chars = lower + upper + digits + symbols
    chars += [random.choice(all_chars) for _ in range(length - 4)]
    random.shuffle(chars)
    return ''.join(chars)


async def click_next(page):
    try:
        next_button = page.locator('button[type="submit"], input[type="submit"], #iSignupAction').first
        await next_button.wait_for(state="visible", timeout=5000)
        await page.wait_for_timeout(random.randint(400, 800))
        await next_button.click()
    except Exception as e:
        raise RuntimeError(f"Failed to click the 'Next' button: {e}")


async def human_type(page, selector, text):
    """Type text character by character with human-like random delays."""
    loc = page.locator(selector).first
    await loc.wait_for(state="visible", timeout=3000)
    await loc.click()
    await loc.fill("")
    for char in text:
        await loc.press(char)
        await page.wait_for_timeout(random.randint(80, 220))


async def fill_first(page, selectors, value):
    for sel in selectors:
        try:
            await human_type(page, sel, value)
            return
        except Exception:
            continue
    raise RuntimeError(f"None of these selectors matched or could be filled: {selectors}")


async def dump_controls(page, context_msg=""):
    try:
        info = await page.evaluate(r"""() => {
            const out = [];
            const els = document.querySelectorAll('select, button, [role="button"], [role="combobox"], input');
            for (const e of els) {
                const txt = (e.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 40);
                out.push(`<${e.tagName.toLowerCase()} id="${e.id}" name="${e.getAttribute('name') || ''}" role="${e.getAttribute('role') || ''}" aria="${e.getAttribute('aria-label') || ''}" type="${e.getAttribute('type') || ''}" opts=${e.tagName === 'SELECT' ? e.options.length : ''} text="${txt}">`);
            }
            return out.join('\n');
        }""")
        print(f"\n[DEBUG] Controls on page {context_msg}:\n{info}\n")
    except Exception as e:
        print(f"\n[DEBUG] Could not dump controls: {e}\n")


async def set_birthdate(page, month: int, day: int, year: int):
    try:
        print("[dbg] Clicking Month dropdown...")
        month_trigger = page.locator('div, button').filter(has_text=re.compile(r"^Month$")).first
        await month_trigger.wait_for(state="visible", timeout=5000)
        await page.wait_for_timeout(random.randint(300, 600))
        await month_trigger.click()
        await page.wait_for_timeout(400)
        
        month_name = MONTH_NAMES[month - 1]
        print(f"[dbg] Selecting month: {month_name}")
        month_option = page.locator(f'[role="option"]:text-is("{month_name}"), div:text-is("{month_name}")').first
        await month_option.wait_for(state="visible", timeout=3000)
        await month_option.click()
        await page.wait_for_timeout(400)

        print("[dbg] Clicking Day dropdown...")
        day_trigger = page.locator('div, button').filter(has_text=re.compile(r"^Day$")).first
        await day_trigger.wait_for(state="visible", timeout=5000)
        await page.wait_for_timeout(random.randint(300, 600))
        await day_trigger.click()
        await page.wait_for_timeout(400)
        
        print(f"[dbg] Selecting day: {day}")
        day_option = page.locator(f'[role="option"]:text-is("{day}"), div:text-is("{day}")').first
        await day_option.wait_for(state="visible", timeout=3000)
        await day_option.click()
        await page.wait_for_timeout(400)

        print(f"[dbg] Typing year: {year}")
        year_box = page.locator('input').last
        await year_box.wait_for(state="visible", timeout=5000)
        await year_box.click()
        await year_box.fill("")
        for char in str(year):
            await year_box.press(char)
            await page.wait_for_timeout(random.randint(100, 250))
        await page.wait_for_timeout(500)
        
    except Exception as e:
        raise RuntimeError(f"Error during birthdate selection: {e}")


async def wait_for_recovery_screen(page, timeout_sec=120):
    print("[+] Waiting for CAPTCHA completion and screen transition...")
    start_time = time.time()
    
    while time.time() - start_time < timeout_sec:
        try:
            current_url = page.url
            if "interrupt" in current_url or "credentialaction" in current_url:
                print(f"[+] Detected recovery URL: {current_url}")
                return True
            
            add_email_btn = page.locator('button, div').filter(has_text=re.compile(r"^Add email$"))
            if await add_email_btn.count() > 0 and await add_email_btn.first.is_visible():
                print("[+] Detected 'Add email' button on screen!")
                return True
                
        except Exception:
            pass
            
        await asyncio.sleep(1)
        
    raise RuntimeError("Timed out waiting for CAPTCHA/recovery screen transition.")


async def get_temp_email_from_tab(context):
    print("[+] Opening 1sec.email in a new tab...")
    mail_tab = await context.new_page()
    
    await context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://1sec.email")
    await mail_tab.goto("https://1sec.email", wait_until="networkidle", timeout=30000)
    
    print("[+] Waiting for Copy button to be ready...")
    copy_btn = mail_tab.locator('button').filter(has_text=re.compile(r"^Copy$", re.IGNORECASE)).first
    await copy_btn.wait_for(state="visible", timeout=15000)
    await mail_tab.wait_for_timeout(1000)
    
    print("[!] [WARNING] Clicking COPY button. Please DO NOT copy anything else until the script finishes pasting!")
    await copy_btn.click()
    await mail_tab.wait_for_timeout(500)
    
    temp_email = await mail_tab.evaluate("async () => await navigator.clipboard.readText()")
    temp_email = temp_email.strip()
    
    if not temp_email or "@" not in temp_email:
        raise RuntimeError("Failed to read temporary email from clipboard.")
        
    print(f"[+] Successfully grabbed temp email via Copy button: {temp_email}")
    return mail_tab, temp_email


async def wait_for_and_extract_code(page, mail_tab):
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        print(f"[+] Monitoring 1sec.email inbox for verification email (Attempt {attempt}/{max_attempts})...")
        await mail_tab.bring_to_front()
        
        start_time = time.time()
        while time.time() - start_time < 45:
            try:
                email_item = mail_tab.locator('div, tr, li').filter(has_text=re.compile(r"Microsoft|security code|verify", re.IGNORECASE)).first
                if await email_item.is_visible():
                    print("[+] Verification email detected in list! Clicking to open...")
                    await email_item.click()
                    await mail_tab.wait_for_timeout(2000)
                    
                    body_text = await mail_tab.locator('body').inner_text()
                    match = re.search(r'(?:Security code|code)[:\s]*(\d{6})', body_text, re.IGNORECASE)
                    if match:
                        return match.group(1)
                        
                    match_fallback = re.search(r'\b\d{6}\b', body_text)
                    if match_fallback:
                        return match_fallback.group(0)
            except Exception:
                pass
            await asyncio.sleep(2)
            
        if attempt < max_attempts:
            print(f"[-] No email received. Hitting 'Resend code' (Attempt {attempt})...")
            await page.bring_to_front()
            await page.wait_for_timeout(1000)
            try:
                resend_btn = page.locator('button, a, div').filter(has_text=re.compile(r"Resend code|send new code", re.IGNORECASE)).first
                await resend_btn.click()
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            
    raise RuntimeError("Timed out waiting for verification email after 3 attempts.")


async def run_generator():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="./playwright_account_profile",
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-popup-blocking",
                "--disable-site-isolation-trials"
            ],
            viewport={"width": 1280, "height": 800}
        )
        
        page = browser.pages[0] if browser.pages else await browser.new_page()
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        full_email = ""
        password = ""
        mail_tab = None
        random_month = 1
        random_day = 1
        random_year = 2000
        chosen_first = ""
        chosen_last = ""

        try:
            print("[+] Navigating to Microsoft signup page...")
            await page.goto("https://signup.live.com/signup", wait_until="domcontentloaded", timeout=30000)

            # Step 1: Email (@outlook.com)
            print("[+] Waiting for email input field to mount...")
            email_selector = 'input[type="email"], input#MemberName'
            await page.wait_for_selector(email_selector, state="visible", timeout=15000)
            await page.wait_for_timeout(1000)

            raw_user = f"user_{generate_random_string(8)}"
            full_email = f"{raw_user}@outlook.com"
            print(f"[+] Entering email: {full_email}")
            
            await fill_first(page, [email_selector], full_email)
            await click_next(page)

            # Step 2: Password
            password = generate_random_password(12)
            print(f"[+] Entering password: {password}")
            await fill_first(page, ['input[type="password"]', 'input#PasswordInput'], password)
            await click_next(page)
            await page.wait_for_timeout(1500)

            # Step 3: Birthdate
            print("[+] Filling birthdate...")
            await page.wait_for_selector('text="Month"', timeout=20000)
            
            random_month = random.randint(1, 12)
            random_day = random.randint(1, 28)
            random_year = random.randint(1985, 2002)
            
            await set_birthdate(page, random_month, random_day, random_year)
            await page.wait_for_timeout(500)
            
            await click_next(page)

            # Step 4: Add randomized name & uncheck spam checkbox
            chosen_first = random.choice(FIRST_NAMES)
            chosen_last = random.choice(LAST_NAMES)
            print(f"[+] Filling name details ({chosen_first} {chosen_last}) and unchecking spam opt-in...")
            await page.wait_for_selector('#firstNameInput', timeout=10000)
            
            await fill_first(page, ['#firstNameInput', 'input#FirstName', 'input[name="FirstName"]'], chosen_first)
            await fill_first(page, ['#lastNameInput', 'input#LastName', 'input[name="LastName"]'], chosen_last)
            
            checkbox = page.locator('#marketingOptIn')
            if await checkbox.is_visible() and await checkbox.is_checked():
                await page.wait_for_timeout(random.randint(300, 600))
                await checkbox.uncheck()
                print("[+] Unchecked marketing opt-in checkbox.")
            
            await page.wait_for_timeout(500)
            await click_next(page)

            # Check for block screen
            print("[+] Waiting 3 seconds to check for account block screen...")
            await page.wait_for_timeout(3000)
            
            block_header = page.locator('text="Account creation has been blocked"').first
            if await block_header.is_visible(timeout=2000):
                print("\n" + "!" * 60)
                print("[x] Account creation by script blocked")
                print("[x] Please try using a good vpn as you can generate 3 accounts per IP")
                print("[x] X-VPN and Cloudflare WARP are good options")
                print("[x] I am currently working on a fix for this problem. Will probably release after opencode-infinity is finished.")
                print("!" * 60 + "\n")
                return

            # Step 5: CAPTCHA completion detection via watcher
            print("\n[!] Paused on CAPTCHA phase.")
            print("[!] Complete the puzzle in the browser window. Script is actively watching for screen change...")
            
            await wait_for_recovery_screen(page, timeout_sec=120)
            print("[+] Successfully transitioned to the recovery screen!")

            # Step 6: Recovery Email Handling via Multi-Tab UI Switching & Copy/Paste
            await page.wait_for_timeout(1500)

            print("[+] Looking for 'Add email' button...")
            add_email_btn = page.locator('button, div').filter(has_text=re.compile(r"^Add email$", re.IGNORECASE)).first
            await add_email_btn.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(random.randint(400, 800))
            await add_email_btn.click()
            print("[+] Clicked 'Add email' button.")
            await page.wait_for_timeout(1500)

            mail_tab, temp_email = await get_temp_email_from_tab(browser)

            print("[+] Switching back to Microsoft tab...")
            await page.bring_to_front()
            await page.wait_for_timeout(1000)

            print(f"[+] Pasting recovery email address: {temp_email}")
            input_box = page.locator('input[id*="floatingLabelInput"], input[type="text"], input[type="email"]').first
            await input_box.wait_for(state="visible", timeout=10000)
            await input_box.click()
            await input_box.fill("")
            await page.keyboard.press("Control+V")
            await page.wait_for_timeout(800)
            
            await click_next(page)

            code = await wait_for_and_extract_code(page, mail_tab)

            print("[+] Switching back to Microsoft tab to enter verification code...")
            await page.bring_to_front()
            await page.wait_for_timeout(1000)

            print(f"[+] Entering 6-digit code digit-by-digit into codeEntry fields...")
            for i, digit in enumerate(code):
                digit_input = page.locator(f'input#codeEntry-{i}').first
                await digit_input.wait_for(state="visible", timeout=5000)
                await digit_input.click()
                await digit_input.fill("")
                await digit_input.press(digit)
                await page.wait_for_timeout(random.randint(100, 250))

            await page.wait_for_timeout(1000)
            try:
                await click_next(page)
            except Exception:
                pass

            print("[+] Recovery email verification completed successfully!")

            # Step 7: Handle post-verification screens (OK and Stay Signed In)
            print("[+] Handling post-verification screens...")
            try:
                ok_btn = page.locator('button, input[type="button"]').filter(has_text=re.compile(r"^OK$", re.IGNORECASE)).first
                if await ok_btn.is_visible(timeout=5000):
                    print("[+] Clicking 'OK' on account notice page...")
                    await page.wait_for_timeout(random.randint(400, 800))
                    await ok_btn.click()
            except Exception:
                pass

            try:
                print("[+] Waiting for 'Stay signed in?' prompt...")
                await page.wait_for_selector('text="Stay signed in?"', timeout=15000)
                await page.wait_for_timeout(1000)
                
                yes_btn = page.locator('button, input[type="button"]').filter(has_text=re.compile(r"^Yes$", re.IGNORECASE)).first
                await yes_btn.wait_for(state="visible", timeout=5000)
                print("[+] Clicking 'Yes' on 'Stay signed in?' page...")
                await page.wait_for_timeout(random.randint(400, 800))
                await yes_btn.click()
            except Exception as e:
                print(f"[-] Could not find or click 'Yes' button: {e}")

            # Step 8: Save account data to JSON
            current_year = 2026
            age = current_year - random_year
            birthday_str = f"{MONTH_NAMES[random_month - 1]} {random_day}, {random_year}"

            account_data = {
                "email": full_email,
                "password": password,
                "name": f"{chosen_first} {chosen_last}",
                "birthday": birthday_str
            }
            with open("accounts.json", "w") as f:
                json.dump(account_data, f, indent=4)
            print("[+] Account successfully saved to accounts.json!")

            print("\n" + "=" * 55)
            print(" 🎉 MICROSOFT ACCOUNT CREATED AND VERIFIED SUCCESSFULLY! 🎉 ")
            print("=" * 55)
            print(f" Username  : {full_email}")
            print(f" Password  : {password}")
            print(f" Full Name : {chosen_first} {chosen_last}")
            print(f" Birthday  : {birthday_str}")
            print("=" * 55 + "\n")

            # ==========================================
            # STEP 9: PUTER.JS OAUTH INTEGRATION FLOW
            # ==========================================
            print("[+] Navigating to Free Claude app to begin Puter.js integration...")
            await page.goto("https://free-claude.42web.io/?i=1", wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            print("[+] Clicking Settings button...")
            settings_btn = page.locator('button, div, a').filter(has_text=re.compile(r"Settings", re.IGNORECASE)).first
            await settings_btn.wait_for(state="visible", timeout=10000)
            await settings_btn.click()
            await page.wait_for_timeout(1500)

            print("[+] Clicking 'Sign in with Puter' button and capturing popup...")
            signin_puter_btn = page.locator('button, div, a').filter(has_text=re.compile(r"Sign in with Puter", re.IGNORECASE)).first
            await signin_puter_btn.wait_for(state="visible", timeout=10000)
            
            async with browser.expect_event("page", timeout=10000) as popup_info:
                await signin_puter_btn.click()
            popup = await popup_info.value

            await popup.wait_for_load_state("domcontentloaded")
            await popup.bring_to_front()
            print("[+] Puter popup detected!")

            print("[+] Clicking 'Create Account' inside Puter popup...")
            create_acc_btn = popup.locator('button, a, div').filter(has_text=re.compile(r"Create Account", re.IGNORECASE)).first
            await create_acc_btn.wait_for(state="visible", timeout=10000)
            await create_acc_btn.click()
            await popup.wait_for_timeout(1500)

            print("[+] Clicking 'Sign up with Microsoft' and capturing OAuth window...")
            ms_signup_btn = popup.locator('button, div, a').filter(has_text=re.compile(r"Sign up with Microsoft", re.IGNORECASE)).first
            await ms_signup_btn.wait_for(state="visible", timeout=10000)
            
            async with browser.expect_event("page", timeout=10000) as oauth_info:
                await ms_signup_btn.click()
            oauth_page = await oauth_info.value

            await oauth_page.wait_for_load_state("domcontentloaded")
            await oauth_page.bring_to_front()

            print(f"[+] Entering username: {full_email}")
            email_input = oauth_page.locator('input[type="email"], input#i0116').first
            await email_input.wait_for(state="visible", timeout=10000)
            await email_input.click()
            await email_input.fill(full_email)
            
            next_btn = oauth_page.locator('button[type="submit"], input[type="submit"], #idSIButton9').first
            await next_btn.click()
            await oauth_page.wait_for_timeout(2000)

            print("[+] Clicking 'Send code to ...@1sec.email' button on OAuth screen...")
            send_code_btn = oauth_page.locator('button, div, input').filter(has_text=re.compile(r"Send a code to", re.IGNORECASE)).first
            await send_code_btn.wait_for(state="visible", timeout=10000)
            await send_code_btn.click()
            await oauth_page.wait_for_timeout(2000)

            print("[+] Locating original 1sec.email tab...")
            target_mail_tab = None
            for p_tab in browser.pages:
                if "1sec.email" in p_tab.url:
                    target_mail_tab = p_tab
                    break

            if not target_mail_tab:
                print("\n[!] [ERROR] The old 1sec.email tab does not exist!")
                print("[+] Automatically generating a fresh 1sec.email tab...")
                target_mail_tab, temp_email = await get_temp_email_from_tab(browser)

            print("[+] Waiting for NEW verification email on 1sec.email...")
            await target_mail_tab.bring_to_front()
            
            oauth_code = None
            start_mail_wait = time.time()
            while time.time() - start_mail_wait < 60:
                try:
                    email_items = target_mail_tab.locator('div, tr, li').filter(has_text=re.compile(r"Microsoft|security code|verify", re.IGNORECASE))
                    count = await email_items.count()
                    if count > 0:
                        await email_items.last.click()
                        await target_mail_tab.wait_for_timeout(2000)
                        
                        body_text = await target_mail_tab.locator('body').inner_text()
                        match = re.search(r'(?:Security code|code)[:\s]*(\d{6})', body_text, re.IGNORECASE)
                        if match:
                            oauth_code = match.group(1)
                            break
                        match_fallback = re.search(r'\b\d{6}\b', body_text)
                        if match_fallback:
                            oauth_code = match_fallback.group(0)
                            break
                except Exception:
                    pass
                await asyncio.sleep(2)

            if not oauth_code:
                raise RuntimeError("Timed out waiting for new OAuth verification code email.")

            print(f"[+] Successfully extracted OAuth verification code: {oauth_code}")

            print("[+] Switching back to OAuth verification screen...")
            await oauth_page.bring_to_front()
            await oauth_page.wait_for_timeout(1000)

            print("[+] Entering OAuth code into verification box...")
            code_input = oauth_page.locator('input[type="text"], input[name="otc"]').first
            if await code_input.count() > 0 and await code_input.is_visible():
                await code_input.fill(oauth_code)
            else:
                for i, digit in enumerate(oauth_code):
                    box = oauth_page.locator(f'input#codeEntry-{i}, input[name="codeEntry-{i}"]').first
                    if await box.is_visible():
                        await box.fill(digit)

            await oauth_page.wait_for_timeout(1000)
            verify_submit = oauth_page.locator('button[type="submit"], input[type="submit"], #idSIButton9').first
            await verify_submit.click()

            print("\n" + "=" * 55)
            print(" 🎉 PUTER.JS MICROSOFT OAUTH LINKING COMPLETED! 🎉 ")
            print("=" * 55)
            print("[+] Stopping script completely as requested.")

        except Exception as e:
            print(f"\n[-] Script paused/stopped due to error: {e}")
            await dump_controls(page, "after failure")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(run_generator())
