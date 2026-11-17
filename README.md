  █▀▀ █▀█ █▀▀ █▀▀ ⠀⠀█▀▀ █   ▄▀█ █ █ █▀▄ █▀▀  
  █▀⠀ █▀▄ ██▄ ██▄ ⠀⠀█▄▄ █▄▄ █▀█ █▄█ █▄▀ ██▄

**FREE CLAUDE** is an automated tool for spamming API trial keys to your local Free Claude Code installation with alt accounts.


---

## Features

| Feature                       | Description                                                               |
| ----------------------------- | ------------------------------------------------------------------------- |
| **Browser automation**        | Uses Playwright and ChromeDriver for browser interaction.                 |
| **Email management**          | Reads configured email information from `assets.txt`.                     |
| **Gmail verification bridge** | Receives verification codes through a local HTTP server and Tampermonkey. |
| **API key management**        | Saves API keys locally in `keys.txt`.                                     |
| **Provider integration**      | Adds keys to the existing trial provider in Free Claude Code.    |
| **AI generated readme**   | How do you right a readme without spelling errors i was last in spelling bee.      |

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- Google Chrome
- Playwright and Chromium
- ChromeDriver
- Free Claude Code running locally
- Tampermonkey browser extension
- A Gmail account
- A Nonecap API key

## Setup

### 1. Download the project

Clone the repository or download its source files.

Open PowerShell in the project directory.

### 2. Configure `assets.txt`

Create or open `assets.txt` in the project folder.

This file stores the local configuration used by the program.
- we reccomend creating a long email if yours is shorter then 9 charecters
- enter your email in the assets.txt file
- enter your Nonecap API key at https://nonecap.com/#get-started (they accept https://tempmailg.com addresses)
- you get 1,300 solves per account

### 3. Set up Tampermonkey

1. Install the Tampermonkey extension from the official browser extension store.
2. Open the Tampermonkey dashboard.
3. Select **Create a new script**.
4. Paste the project's Gmail bridge userscript into the editor.
5. Save the script.
6. Open Gmail in Chrome.
7. hit ctrl+shift+r if the script dosent load initially (it has a UI)
8. keep this tab running in the background.

The userscript communicates with the local verification bridge. Only install scripts you trust, and review their permissions before enabling them.

### 4. Configure NoneCap

How to get a nonecap trial key

1. Open the official NoneCap at https://nonecap.com .
2. Create an account with https://tempmailg.com as your email address.
3. Complete the account setup.
4. Generate an API key through your account dashboard.
5. Enter it in assets.txt as nonecap= OR nonecapapi=


### 5. Start Free Claude Code

Make sure Free Claude Code is running locally and that its admin panel is accessible.
- If it is not installed api-gen.py will guide you through install steps.

Default address:

```text
http://localhost:8082/admin
```

### 6. Run the program

```powershell
python api-gen.py
```

The program checks its dependencies, starts its local services, and opens the admin panel.

## Local Files

| File / Folder  | Description             |
| -------------- | ----------------------- |
| `api-gen.py`   | Main program            |
| `assets.txt`   | Local configuration     |
| `keys.txt`     | Locally stored API keys |
| `accounts.txt` | Recent account history  |
| `drivers/`     | Browser driver files    |

## Ports

| Port   | Purpose                         |
| ------ | ------------------------------- |
| `8080` | Local Gmail verification bridge |
| `8082` | Free Claude Code admin panel    |


## Credits

- **NoneCap** — CAPTCHA service
- **Playwright** — Browser automation
- **Tampermonkey** — Browser userscript support
- **Free Claude Code** — Local application integration
