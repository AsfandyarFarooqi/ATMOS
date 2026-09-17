"""Lazy, thread-safe Earth Engine initialisation.

The previous code called ``ee.Authenticate()`` at module import time, which
opens a browser on every import (twice under Flask's auto-reloader) and hangs
outright in any non-interactive environment. Here we initialise on first use,
and only fall back to the interactive auth flow when stored credentials are
genuinely missing.
"""
import logging
import threading

import ee

from config import EE_PROJECT

log = logging.getLogger(__name__)

_lock = threading.Lock()
_ready = False


def ensure_initialized() -> None:
    """Initialise Earth Engine once per process. Safe to call from any thread."""
    global _ready
    if _ready:
        return

    with _lock:
        if _ready:  # another thread won the race
            return
        if not EE_PROJECT:
            raise RuntimeError(
                "EE_PROJECT is not set. Copy .env.example to .env and set "
                "EE_PROJECT to your Earth Engine Cloud project id."
            )
        try:
            ee.Initialize(project=EE_PROJECT)
        except Exception as exc:
            log.warning("Earth Engine not initialised (%s); starting auth flow.", exc)
            ee.Authenticate()
            ee.Initialize(project=EE_PROJECT)
        log.info("Earth Engine initialised for project %s", EE_PROJECT)
        _ready = True
