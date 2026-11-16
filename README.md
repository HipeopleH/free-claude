# Free Claude Code

<p align="center">
  **Ace Data Cloud → Free Claude Code**
  <br>
  An automated tool for connecting Ace Data Cloud API keys to your local Free Claude Code installation.
</p>

<p align="center">
  ![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
  ![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
  ![Browser](https://img.shields.io/badge/Browser-Playwright-2EAD33?logo=playwright&logoColor=white)
</p>

---

## Features

| Feature | Description |
|---|---|
| **Automated registration** | Handles the Ace Data Cloud registration workflow. |
| **Email management** | Generates Gmail address variations and tracks account history. |
| **hCaptcha integration** | Integrates with NoneCap for challenge-token handling. |
| **Gmail verification bridge** | Receives verification codes through a local HTTP server and Tampermonkey. |
| **Automatic key capture** | Retrieves newly generated API keys and saves them locally. |
| **Provider integration** | Adds keys to the existing Ace Data Cloud provider in Free Claude Code. |
| **Non-destructive updates** | Adds new key rows without intentionally replacing existing entries. |

## How It Works

The program connects the registration process, email verification, API key capture, and local Free Claude Code configuration into one workflow.

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- Google Chrome
- Playwright and its Chromium browser
- ChromeDriver
- Free Claude Code running locally
- A configured Tampermonkey Gmail bridge

## Quick Start

**1. Open the project folder**

Open PowerShell in the directory containing `coolfile.py`.

**2. Run the program**

```powershell
python coolfile.py
```

**3. Follow the terminal**

The program checks its dependencies, starts its local services, and opens the Free Claude Code admin panel.

**4. Check your configuration**

Confirm that the new key appears in your existing Ace Data Cloud provider configuration.

## Local Files

| File / Folder | Description |
|---|---|
| `coolfile.py` | Main program |
| `keys.txt` | Stores captured API keys |
| `accounts.txt` | Stores recent account history |
| `drivers/` | Browser driver files |

## Configuration & Security

- Keep `keys.txt` private.
- Never upload API keys or account passwords to GitHub.
- Do not share verification codes or authentication tokens.
- API availability and credit balances are controlled by Ace Data Cloud.
- The local bridge should only be accessible to trusted local applications.

## Credits

- **Ace Data Cloud** — API provider
- **NoneCap** — CAPTCHA token service integration
- **Playwright** — Browser automation
- **Tampermonkey** — Gmail userscript support
- **Free Claude Code** — Local application integration

---

<p align="center">
  <sub>Built for Free Claude Code.</sub>
</p>
