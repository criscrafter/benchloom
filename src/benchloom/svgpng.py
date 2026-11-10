# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Friedrich-Christer Rosenthal
"""Turn SVG images into PNG files next to them: ``python -m benchloom.svgpng a.svg b.svg``.

Runs as its own short process at the end of a run, so the test process never
loads Qt. Needs PySide6 (the desktop app's extra).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(paths: list[str]) -> int:
    from PySide6.QtCore import QByteArray
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    app = QGuiApplication.instance() or QGuiApplication(["benchloom-svgpng"])  # noqa: F841
    failed = 0
    for name in paths:
        path = Path(name)
        renderer = QSvgRenderer(QByteArray(path.read_bytes()))
        if not renderer.isValid():
            failed += 1
            continue
        size = renderer.defaultSize()
        image = QImage(size.width() * 2, size.height() * 2, QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter)
        painter.end()
        if not image.save(str(path.with_suffix(".png"))):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
