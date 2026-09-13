#!/usr/bin/env python3
# Copyright (C) 2026 ChiefGyk3D
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load applet config pages headlessly and fail on any QML error or warning.

The pages never reference the `plasmoid` context object, so they load
outside plasmashell; `i18n` is stubbed on the JS global object. Needs the
distro's python3-pyqt6 (uses the system Qt and its KDE QML modules). Not
part of the unit suite -- `tests/` stays stdlib-only.

    python3 plasmoid/tools/load-page.py plasmoid/package/contents/ui/config*.qml
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QTimer, QUrl                      # noqa: E402
from PyQt6.QtGui import QGuiApplication                    # noqa: E402
from PyQt6.QtQml import QQmlComponent, QQmlEngine          # noqa: E402

I18N_STUB = ("(function(s){var a=arguments;return String(s)"
             ".replace(/%(\\d)/g,function(m,n){return a[+n];});})")
NOISE = ("Failed to find a Kirigami platform plugin",)


def load(engine, path):
    bad = []
    engine.warnings.connect(lambda errs: bad.extend(str(e) for e in errs))
    comp = QQmlComponent(engine, QUrl.fromLocalFile(os.path.abspath(path)))
    if comp.isError():
        return [str(e) for e in comp.errors()]
    obj = comp.create()
    app = QGuiApplication.instance()
    QTimer.singleShot(400, app.quit)
    app.exec()
    if obj is None:
        bad.append("create() returned null")
    else:
        obj.deleteLater()
    return [b for b in bad if not any(n in b for n in NOISE)]


def main(paths):
    app = QGuiApplication(sys.argv[:1])   # noqa: F841 (must outlive the engine)
    engine = QQmlEngine()
    for name in ("i18n", "i18nc", "i18np", "i18nd"):
        engine.globalObject().setProperty(name, engine.evaluate(I18N_STUB))
    rc = 0
    for p in paths:
        problems = load(engine, p)
        if problems:
            rc = 1
            print(f"FAIL {p}")
            for line in problems:
                print("   ", line)
        else:
            print(f"ok {p}")
    return rc


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(main(sys.argv[1:]))
