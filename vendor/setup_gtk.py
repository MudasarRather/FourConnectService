"""One-shot Windows GTK3 bootstrapper for WeasyPrint.

WeasyPrint depends on Pango / GLib / HarfBuzz, which on Windows ship as GTK3
DLLs. Rather than asking every developer to run the tschoonj installer, this
script pulls the latest GTK3 binary packages from the MSYS2 mirror and unpacks
just the DLLs into ``vendor/gtk-runtime/bin/``.

Run once after ``pip install -r requirements.txt``::

    python vendor/setup_gtk.py

After that, ``app/utils/gtk_bootstrap.py`` finds the DLLs automatically when
the backend starts and WeasyPrint imports cleanly.

The script is idempotent — re-running skips packages already extracted, and
auto-resolves the newest version of each package by scraping the MSYS2 mirror
directory listing (so it doesn't rot when MSYS2 bumps versions).

Total download is ~50 MB; on-disk footprint after extraction is ~80 MB.
"""
from __future__ import annotations

import io
import os
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

VENDOR_ROOT = Path(__file__).resolve().parent
GTK_DIR = VENDOR_ROOT / "gtk-runtime"
BIN_DIR = GTK_DIR / "bin"

MIRROR = "https://repo.msys2.org/mingw/mingw64/"

# Package base names (without version). Order matters only for readability —
# extraction is parallel-safe so re-running won't reshuffle anything.
PACKAGE_BASES = [
    # Core runtime
    "gcc-libs",
    "libwinpthread-git",
    "zlib",
    "libffi",
    "libiconv",            # libintl→libiconv-2.dll
    "gettext-runtime",
    "pcre2",
    "expat",
    "bzip2",
    "libpng",
    # GLib (provides libgobject, libgio, libglib)
    "glib2",
    # Text rendering stack
    "fribidi",
    "freetype",
    "fontconfig",
    "harfbuzz",
    "graphite2",
    "brotli",
    "libdatrie",           # libthai→libdatrie-1.dll
    "libthai",             # pango→libthai-0.dll (script-shaping for Thai)
    "pango",
    # Cairo (referenced by some WeasyPrint code paths via cairocffi fallback)
    "pixman",
    "cairo",
]


def _ensure_zstandard():
    try:
        import zstandard  # noqa: F401
    except ImportError:
        import subprocess
        print("Installing zstandard (needed to unpack MSYS2 .pkg.tar.zst)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "zstandard"])


def _fetch_mirror_listing() -> str:
    print(f"Indexing {MIRROR} ...")
    with urllib.request.urlopen(MIRROR, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _latest_filename(listing: str, base: str) -> str | None:
    """Find the highest-versioned .pkg.tar.zst for `base` in the directory listing."""
    pattern = re.compile(
        rf'mingw-w64-x86_64-{re.escape(base)}-([0-9][0-9a-z._+]*?)-([0-9]+)-any\.pkg\.tar\.zst'
    )
    matches = pattern.findall(listing)
    if not matches:
        return None

    def _key(m):
        ver, rel = m
        # Split version into numeric tuples for natural ordering
        parts = []
        for token in re.split(r'[._+r]', ver):
            try:
                parts.append((0, int(token)))
            except ValueError:
                parts.append((1, token))  # non-numeric sorts after numeric
        return (parts, int(rel))

    matches.sort(key=_key)
    ver, rel = matches[-1]
    return f"mingw-w64-x86_64-{base}-{ver}-{rel}-any.pkg.tar.zst"


def _download(url: str) -> bytes:
    print(f"  fetching {url.split('/')[-1]}")
    with urllib.request.urlopen(url, timeout=180) as resp:
        return resp.read()


def _extract_dlls(data: bytes):
    import zstandard
    dctx = zstandard.ZstdDecompressor()
    with dctx.stream_reader(io.BytesIO(data)) as reader:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if not member.isfile():
                    continue
                name = member.name
                # We only want DLLs from mingw64/bin
                if not name.startswith("mingw64/bin/") or not name.endswith(".dll"):
                    continue
                target = BIN_DIR / Path(name).name
                if target.exists():
                    continue
                f = tar.extractfile(member)
                if f is None:
                    continue
                target.write_bytes(f.read())


def main():
    if sys.platform != "win32":
        print("Not on Windows — system Pango/Cairo is used; nothing to do.")
        return

    _ensure_zstandard()
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    listing = _fetch_mirror_listing()

    missing = []
    for base in PACKAGE_BASES:
        fname = _latest_filename(listing, base)
        if not fname:
            print(f"  WARNING: no package found for {base}")
            missing.append(base)
            continue
        try:
            data = _download(MIRROR + fname)
            _extract_dlls(data)
        except Exception as e:
            print(f"  WARNING {fname} failed: {e}")
            missing.append(base)

    dll_count = len(list(BIN_DIR.glob("*.dll")))
    print(f"\nExtracted {dll_count} DLLs to {BIN_DIR}")
    if missing:
        print(f"  Skipped packages: {missing}")

    print("\nVerifying WeasyPrint import…")
    os.environ["PATH"] = str(BIN_DIR) + os.pathsep + os.environ.get("PATH", "")
    try:
        os.add_dll_directory(str(BIN_DIR))
    except Exception:
        pass
    try:
        import weasyprint
        weasyprint.HTML(string="<html><body><p>ok</p></body></html>").write_pdf()
        print("  WeasyPrint OK — PDF rendering works.")
    except Exception as e:
        print(f"  Still failing: {type(e).__name__}: {e}")
        print("  Some DLL may be missing — check vendor/gtk-runtime/bin/ contents.")
        sys.exit(1)


if __name__ == "__main__":
    main()
