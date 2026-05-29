"""Make WeasyPrint find GTK3 DLLs on Windows.

WeasyPrint shells out to libpango / libgobject / libharfbuzz at import time.
On Linux/macOS these are packaged with the OS, so this module is a no-op.
On Windows they ship with GTK3 — we look for them in three places, in order:

  1. ``vendor/gtk-runtime/bin/``  (repo-bundled DLLs, see ``vendor/setup_gtk.py``)
  2. ``C:/Program Files/GTK3-Runtime Win64/bin``  (tschoonj installer default)
  3. ``%PATH%``  (already on PATH — nothing to do)

If we find a candidate, we prepend it to ``os.environ['PATH']`` AND call
``os.add_dll_directory()`` so Python 3.8+'s tightened DLL search picks it up
when ``ctypes`` loads ``libgobject-2.0-0.dll`` inside WeasyPrint.

Idempotent: safe to call from ``main.py`` startup; subsequent calls are no-ops.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BOOTSTRAPPED = False
_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent  # C:/Projects/FourConnectService

CANDIDATES = [
    _BACKEND_ROOT / "vendor" / "gtk-runtime" / "bin",
    Path(r"C:/Program Files/GTK3-Runtime Win64/bin"),
    Path(r"C:/msys64/mingw64/bin"),
]

SENTINEL_DLLS = ("libgobject-2.0-0.dll", "libpango-1.0-0.dll")


def _has_required_dlls(path: Path) -> bool:
    return path.is_dir() and all((path / dll).exists() for dll in SENTINEL_DLLS)


def ensure_gtk_runtime() -> Path | None:
    """Wire up the GTK runtime on Windows. Returns the directory used, or None.

    Safe to call repeatedly. On non-Windows platforms returns None immediately
    and assumes the system already has Pango/Cairo available.
    """
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return None
    _BOOTSTRAPPED = True

    if sys.platform != "win32":
        return None

    for cand in CANDIDATES:
        if _has_required_dlls(cand):
            # Prepend to PATH so child processes inherit the lookup
            os.environ["PATH"] = str(cand) + os.pathsep + os.environ.get("PATH", "")
            # Python 3.8+: explicit allow-list for ctypes DLL loads
            try:
                os.add_dll_directory(str(cand))
            except (AttributeError, FileNotFoundError, OSError):
                # add_dll_directory missing (very old Python) or path vanished
                # between the exists() check and the call — fall back to PATH only
                pass
            return cand

    # No GTK found. We don't raise — the backend should still boot, only the
    # /export/pdf endpoints will fail with a clear error when they try to
    # import weasyprint. Callers can detect the unbootstrapped state by
    # checking weasyprint_available() below.
    return None


def weasyprint_available() -> bool:
    """Cheap probe — try to import WeasyPrint without raising."""
    try:
        ensure_gtk_runtime()
        import weasyprint  # noqa: F401
        return True
    except Exception:
        return False
