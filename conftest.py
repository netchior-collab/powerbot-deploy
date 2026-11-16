from __future__ import annotations

import os
from pathlib import Path


def pytest_configure(config) -> None:
    """Keep pytest temp files inside the writable project workspace.

    Some managed Windows runtimes expose a system temp directory that exists but
    cannot be scanned. A per-process directory also avoids reusing an inaccessible
    temp tree left by another Python runtime.
    """
    if config.option.basetemp is None:
        root = Path(__file__).resolve().parent / "test-temp"
        root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(root / f"pytest-{os.getpid()}")
