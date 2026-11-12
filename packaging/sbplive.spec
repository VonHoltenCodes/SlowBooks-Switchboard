# PyInstaller spec: one console executable, sbplive (sbplive.exe on
# Windows). Build from the repository root:
#
#   python -m PyInstaller --noconfirm --clean packaging/sbplive.spec
#
# The program imports only the standard library, so nothing is collected
# beyond the package itself; the excludes keep PyInstaller from pulling in
# modules the standard library offers but the program never uses.
# A console executable, because a host speaks to it on stdin and stdout.
#
# Unsigned unless SBPLIVE_CODESIGN_IDENTITY names a macOS signing identity
# (the release build's Developer ID): PyInstaller then signs every binary it
# collects and the program itself, with the hardened runtime and a
# timestamp, ready for notarization. Windows signing happens after the build
# (Azure Trusted Signing in the release workflow).

import os

ROOT = os.path.dirname(os.path.abspath(SPECPATH))

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data", "lib2to3", "idlelib", "test"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="sbplive",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    codesign_identity=os.environ.get("SBPLIVE_CODESIGN_IDENTITY") or None,
    entitlements_file=None,
)
