import subprocess
import sys
from pathlib import Path


def start_vaultbot_backend():
    """Start the VaultBot backend in its own detached process."""
    vault2_dir = Path(__file__).parent.resolve()
    venv_python = vault2_dir / "vaultbot_venv" / "Scripts" / "python.exe"
    main_py = vault2_dir / "vaultbot_backend" / "main.py"

    if not venv_python.exists():
        raise FileNotFoundError(f"VaultBot virtualenv python not found: {venv_python}")
    if not main_py.exists():
        raise FileNotFoundError(f"VaultBot backend not found: {main_py}")

    # On Windows use DETACHED_PROCESS so closing Obsidian doesn't kill the server.
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB

    return subprocess.Popen(
        [str(venv_python), str(main_py)],
        cwd=str(vault2_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


if __name__ == "__main__":
    proc = start_vaultbot_backend()
    print(proc.pid)
