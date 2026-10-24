"""Render the favicon set from static/icons/favicon.svg using headless Chrome (no image libraries needed).

Outputs in static/icons/: favicon.ico (16/32/48), favicon-32.png, apple-touch-icon.png (180, full-bleed),
icon-192.png, icon-512.png.

    .venv/bin/python scripts/build_icons.py
"""
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

ICONS = Path(__file__).resolve().parent.parent / "static" / "icons"
SVG = ICONS / "favicon.svg"
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


def render(svg: Path, size: int, out: Path) -> None:
    page = svg.with_suffix(".html")
    page.write_text(f'<html><body style="margin:0"><img src="{svg.as_uri()}" '
                    'style="display:block;width:100vw;height:100vh"></body></html>')
    subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files",
                    "--default-background-color=00000000", "--force-device-scale-factor=1",
                    f"--window-size={size},{size}", f"--screenshot={out}", page.as_uri()],
                   check=True, capture_output=True)


def pack_ico(pngs: list[tuple[int, bytes]], out: Path) -> None:
    """ICO container with embedded PNG images (supported by every current browser and OS)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for size, data in pngs:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    out.write_bytes(header + entries + blobs)


def main() -> None:
    if not CHROME:
        raise SystemExit("Needs google-chrome or chromium on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rounded = tmp / "rounded.svg"
        rounded.write_text(SVG.read_text())
        # iOS rounds home-screen icons itself, so the touch icon is full-bleed (square corners).
        square = tmp / "square.svg"
        square.write_text(SVG.read_text().replace('rx="14"', 'rx="0"', 1))

        ico = []
        for size in (16, 32, 48):
            png = tmp / f"{size}.png"
            render(rounded, size, png)
            ico.append((size, png.read_bytes()))
        pack_ico(ico, ICONS / "favicon.ico")
        (ICONS / "favicon-32.png").write_bytes(ico[1][1])
        render(square, 180, ICONS / "apple-touch-icon.png")
        render(rounded, 192, ICONS / "icon-192.png")
        render(rounded, 512, ICONS / "icon-512.png")
    for f in sorted(ICONS.iterdir()):
        print(f"{f.name:24} {f.stat().st_size:>7} bytes")


if __name__ == "__main__":
    main()
