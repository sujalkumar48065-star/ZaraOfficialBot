"""Optional local ``.env`` loading.

Render and production deployments should set real environment variables. For
local development, a ``.env`` file in the project root is loaded without
overriding anything already exported by the shell.
"""

from __future__ import annotations

from pathlib import Path


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / ".env", override=False)
