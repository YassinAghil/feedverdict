# File locations used by the app, with an override for tests and deployments.

from __future__ import annotations

import os
from pathlib import Path


def app_home() -> Path:
    override = os.environ.get("FEEDVERDICT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "feedverdict"
