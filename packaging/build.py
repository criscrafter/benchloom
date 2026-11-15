# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Build the download for this system:

    Windows: build/Benchloom-<version>-windows-x64.zip
    Linux:   build/Benchloom-<version>-linux-x86_64.tar.gz

    pip install -e ".[gui,usb,visa]" pyinstaller
    python packaging/build.py

It builds the folder with PyInstaller, adds the licence files, runs the self-test
of the result (a test run on simulators, with waveform PNGs and a report) and packs
it. The build fails if the self-test does. On Linux the files keep their execute
permission in the .tar.gz (a zip would lose it). Build Linux on the oldest system
it should run on: the result needs that glibc or a newer one.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BUILD = ROOT / "build"
VERSION = re.search(r'__version__ = "([^"]+)"',
                    (ROOT / "src/benchloom/__init__.py").read_text(encoding="utf-8")).group(1)


def make_icon() -> None:
    """The app icon: a loom of wires on a blue tile, drawn by Qt (kept in the repo)."""
    icon = HERE / "benchloom.ico"
    if icon.exists():
        return
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2563eb"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)
    for i, color in enumerate(("#ffffff", "#fdba74", "#86efac")):  # three woven wires
        pen = QPen(QColor(color), 18, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        path = QPainterPath(QPointF(48, 80 + i * 48))
        path.cubicTo(QPointF(110, 20 + i * 48), QPointF(146, 160 + i * 48),
                     QPointF(208, 96 + i * 48))
        p.drawPath(path)
    p.end()
    image.save(str(icon))


def write_version_info() -> None:
    numbers = [int(n) for n in re.findall(r"\d+", VERSION)[:3]] + [0]
    while len(numbers) < 4:
        numbers.append(0)
    t = tuple(numbers[:4])
    (HERE / "version_info.txt").write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={t}, prodvers={t}),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Friedrich-Christer Rosenthal'),
    StringStruct('FileDescription', 'Benchloom lab automation'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('LegalCopyright', 'Copyright (C) 2026 Friedrich-Christer Rosenthal. AGPL-3.0-or-later.'),
    StringStruct('ProductName', 'Benchloom'),
    StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
""", encoding="utf-8")


WINDOWS = sys.platform == "win32"


def pack(app: Path) -> Path:
    """The folder as the download: a zip on Windows, a .tar.gz elsewhere."""
    if WINDOWS:
        archive = BUILD / f"Benchloom-{VERSION}-windows-x64.zip"
        archive.unlink(missing_ok=True)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for file in sorted(app.rglob("*")):
                if file.is_file():
                    z.write(file, Path("Benchloom") / file.relative_to(app))
        return archive
    system = platform.system().lower()
    archive = BUILD / f"Benchloom-{VERSION}-{system}-{platform.machine()}.tar.gz"
    archive.unlink(missing_ok=True)

    def owned_by_nobody(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info

    with tarfile.open(archive, "w:gz", compresslevel=9) as tar:
        tar.add(app, "Benchloom", filter=owned_by_nobody)
    return archive


def main() -> int:
    make_icon()
    if WINDOWS:
        write_version_info()
    dist = BUILD / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", str(dist), "--workpath", str(BUILD / "work"),
                    str(HERE / "benchloom.spec")], check=True)
    app = dist / "Benchloom"
    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", app / "THIRD_PARTY_NOTICES.md")
    shutil.copy2(ROOT / "CHANGELOG.md", app / "CHANGELOG.md")
    readme = "README-Windows.txt" if WINDOWS else "README-Linux.txt"
    shutil.copy2(HERE / readme, app / "README.txt")

    print("Self-test of the build ...", flush=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("QT_")}  # as users have it
    cli = app / ("benchloom-cli.exe" if WINDOWS else "benchloom-cli")
    result = subprocess.run([str(cli), "--selftest"], env=env)
    if result.returncode != 0:
        print("The self-test failed; no download was made.")
        return 1

    archive = pack(app)
    print(f"{archive} ({archive.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
