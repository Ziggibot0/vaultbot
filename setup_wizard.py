#!/usr/bin/env python3
"""VaultBot one-click setup wizard.

This is the ONLY thing a new user needs to run. It:
  1. Checks Python is installed and recent enough.
  2. Creates the `vaultbot_venv` virtual environment (no manual activate).
  3. Installs all backend dependencies INTO that venv (no global pollution).
  4. Copies `.env.example` -> `.env` if no `.env` exists yet.
  5. Asks for the user's name and writes it into `.env` as VAULTBOT_OWNER.
  6. Reminds the user to install Ollama + pull models (with a `y` to do it
     automatically if `ollama` is on PATH).
  7. Tells the user to open the folder in Obsidian.

Double-click `Setup VaultBot.bat` (Windows) or `Setup VaultBot.command`
(macOS) to launch it. You can also run `python setup_wizard.py` from any
terminal. No venv activation, no pip commands, no terminal skills needed.

Exit codes:
  0  setup complete (or already set up + user chose to skip)
  1  fatal error (Python missing, deps failed, etc.)

Works on Windows, macOS, and Linux. Pure standard library — no deps needed
to run the wizard itself (it bootstraps the venv that holds the real deps).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent
VENV_DIR = VAULT_ROOT / "vaultbot_venv"
BACKEND_DIR = VAULT_ROOT / "vaultbot_backend"
REQUIREMENTS = BACKEND_DIR / "requirements.txt"
ENV_EXAMPLE = VAULT_ROOT / ".env.example"
ENV_FILE = VAULT_ROOT / ".env"
MIN_PY = (3, 11)

# Models the plugin/backend expect by default. The wizard offers to pull them.
DEFAULT_LLM_MODEL = "qwen3.6:latest"
DEFAULT_EMBED_MODEL = "nomic-embed-text"


# ─── pretty printing ──────────────────────────────────────────────────────

def banner(text: str) -> None:
    line = "═" * max(len(text) + 4, 60)
    print(f"\n╔{line}╗")
    print(f"║  {text}{' ' * (len(line) - 2 - len(text))}║")
    print(f"╚{line}╝")


def step(n: int, total: int, text: str) -> None:
    print(f"\n[{n}/{total}] {text}")


def ok(text: str) -> None:
    print(f"  ✅ {text}")


def warn(text: str) -> None:
    print(f"  ⚠️  {text}")


def err(text: str) -> None:
    print(f"  ❌ {text}")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"  {prompt}{suffix}: ").strip()
    return val or default


def yes(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} ({hint}): ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


# ─── helpers ──────────────────────────────────────────────────────────────

def venv_python() -> Path:
    """Path to the venv's python executable (cross-platform)."""
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_pip() -> list[str]:
    """A command list that runs pip INSIDE the venv without activating it."""
    py = venv_python()
    return [str(py), "-m", "pip"]


def set_env_var(name: str, value: str) -> None:
    """Set/replace a KEY=VALUE line in .env (create the file if missing)."""
    if not ENV_FILE.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    out, found = [], False
    for line in lines:
        if line.strip().startswith(f"{name}=") and not line.strip().startswith("#"):
            out.append(f"{name}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")


def get_env_var(name: str) -> str:
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^\s*{re.escape(name)}\s*=\s*(.*?)\s*$", line)
        if m:
            return m.group(1)
    return ""


# ─── steps ────────────────────────────────────────────────────────────────

def check_python() -> bool:
    if sys.version_info < MIN_PY:
        err(f"Python {MIN_PY[0]}.{MIN_PY[1]}+ required, found {sys.version.split()[0]}.")
        print("      Install it from https://www.python.org/downloads/")
        print('      On the installer, check "Add Python to PATH".')
        return False
    ok(f"Python {sys.version.split()[0]} detected.")
    return True


def create_venv() -> bool:
    if venv_python().exists():
        ok("Virtual environment already exists — reusing it.")
        return True
    try:
        subprocess.check_call(
            [sys.executable, "-m", "venv", str(VENV_DIR)],
            stdout=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        err(f"Could not create the virtual environment: {e}")
        return False
    ok("Virtual environment created at vaultbot_venv/.")
    return True


def install_deps() -> bool:
    if not REQUIREMENTS.exists():
        err(f"requirements.txt not found at {REQUIREMENTS}")
        return False
    print("  Installing dependencies (this can take 5–15 minutes the first time)…")
    try:
        subprocess.check_call(
            venv_pip() + ["install", "--upgrade", "pip"],
            stdout=subprocess.DEVNULL,
        )
        subprocess.check_call(
            venv_pip() + ["install", "-r", str(REQUIREMENTS)],
        )
    except subprocess.CalledProcessError as e:
        err(f"Dependency installation failed: {e}")
        print("      Try re-running the wizard. If it keeps failing, check your internet.")
        return False
    ok("All dependencies installed.")
    return True


def configure_env() -> bool:
    if not ENV_FILE.exists():
        if not ENV_EXAMPLE.exists():
            warn(".env.example not found — skipping config file.")
            return True
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        ok("Copied .env.example -> .env.")
    else:
        ok(".env already exists — keeping your settings.")

    current = get_env_var("VAULTBOT_OWNER")
    if current:
        keep = yes(f"VAULTBOT_OWNER is already set to '{current}'. Keep it?", default=True)
        if keep:
            return True
    name = ask("What should VaultBot call you? (your name)", default=current or "")
    if name:
        set_env_var("VAULTBOT_OWNER", name)
        ok(f"VaultBot will address you as '{name}'.")
    else:
        warn("No name set — VaultBot will call you 'the user' until it learns.")
    return True


def check_ollama() -> None:
    ollama = shutil.which("ollama")
    if not ollama:
        warn("Ollama not found on PATH.")
        print("      VaultBot uses Ollama to run the AI model locally.")
        print("      Install it from https://ollama.com, then run this wizard again")
        print("      (or run: ollama pull qwen3.6:latest  &&  ollama pull nomic-embed-text)")
        return
    ok("Ollama detected.")
    pull = yes("Pull the default models now? (~2 GB, one-time)", default=True)
    if not pull:
        return
    for model in (DEFAULT_LLM_MODEL, DEFAULT_EMBED_MODEL):
        print(f"  → ollama pull {model} (this may take a while)…")
        try:
            subprocess.check_call(["ollama", "pull", model])
        except subprocess.CalledProcessError as e:
            warn(f"Could not pull {model}: {e}. You can retry later.")
        except FileNotFoundError:
            warn("Ollama disappeared mid-pull. Run `ollama pull` yourself later.")
    ok("Models pulled.")


def finished() -> None:
    banner("Setup complete!")
    print("\n  Next step: open this folder in Obsidian.")
    print("   1. Open Obsidian → 'Open folder as vault' → pick this folder.")
    print("   2. Settings → Community plugins → turn off Restricted mode.")
    print("   3. Find 'VaultBot' in the list → toggle it ON.")
    print("   4. Click the VaultBot robot icon in the left sidebar → say hi.")
    print("\n  You never need to touch a terminal again. 🎉")


# ─── main ─────────────────────────────────────────────────────────────────

def main() -> int:
    banner("VaultBot Setup Wizard")
    print("  This will set up everything VaultBot needs on this computer.")
    print("  You won't have to run any terminal commands yourself.\n")

    total = 4
    if not check_python():
        return 1
    step(1, total, "Creating the Python environment (vaultbot_venv)…")
    if not create_venv():
        return 1
    step(2, total, "Installing VaultBot's dependencies (one-time, ~5–15 min)…")
    if not install_deps():
        return 1
    step(3, total, "Configuring your settings…")
    if not configure_env():
        return 1
    step(4, total, "Checking the AI model (Ollama)…")
    check_ollama()

    finished()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nSetup cancelled. Re-run the wizard any time.")
        sys.exit(1)