"""Contract tests: shared contracts between the plugin and the backend
must agree. These catch the class of bug where one side reads a value
from a path/config that the other side writes to — and they silently
diverge.

Bug caught: 2026-07-30, the saved LLM model was written to
``vaultbot_backend/.env`` by ``routers/llm.py``'s ``_ENV_PATH`` but the
backend booted reading ``Vault2/.env`` (the vault root, one level up).
The user's picked model in settings was silently ignored on every
restart. See /memories/repo/env-path-mismatch-bug.md.

These tests do NOT import ``main`` (the conftest hard-fence blocks that).
They import only leaf modules (``routers.llm``) and replicate the path
expressions, then assert the two sides agree.
"""

from pathlib import Path

import routers.llm as llm


# main.py's boot-time dotenv path (replicated from main.py:58):
#   dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
# __file__ there is vaultbot_backend/main.py, so this resolves to
# Vault2/.env (the vault root).
BACKEND_DIR = Path(__file__).resolve().parent.parent  # vaultbot_backend/
MAIN_DOTENV_PATH = (BACKEND_DIR / ".." / ".env").resolve()


def test_env_path_main_reads_and_llm_writes_the_same_file():
    """The .env that main.py loads at boot (``load_dotenv``) and the .env
    that routers/llm.py's ``_ENV_PATH`` writes to (via /set_model and
    /llm/config POST) MUST resolve to the same file. If they diverge, a
    model the user picks in settings is persisted to one file but the
    backend boots reading the other — the pick is silently lost on every
    restart and the boot-time default wins.

    This is the exact bug that took the backend down: the saved cloud
    model was ignored because the two paths pointed at different files.
    """
    # llm._ENV_PATH is resolved (Path.resolve()) so compare resolved paths.
    assert llm._ENV_PATH == MAIN_DOTENV_PATH, (
        f".env path mismatch: main.py loads {MAIN_DOTENV_PATH} at boot, "
        f"but routers/llm.py writes to {llm._ENV_PATH}. A setting saved by "
        f"the plugin (/set_model) would be silently ignored on restart. "
        f"Both must resolve to the vault-root .env.")


def test_env_path_is_under_vault_root_not_backend_dir():
    """The shared .env must live at the vault root (one level above
    vaultbot_backend/), NOT inside vaultbot_backend/. The backend dir is
    version-controlled and may be replaced on update; the vault-root .env
    is the user's persistent config. This also matches main.py's
    ``load_dotenv`` target.
    """
    assert llm._ENV_PATH.parent == BACKEND_DIR.parent, (
        f".env should be at the vault root ({BACKEND_DIR.parent}), not "
        f"inside vaultbot_backend/. Got {llm._ENV_PATH}")