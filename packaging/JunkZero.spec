# PyInstaller spec for the single-file JunkZero.exe (Windows) and JunkZero.app (macOS).
# Build from the repository root:  pyinstaller --noconfirm packaging/JunkZero.spec
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

root = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "junkzero.py")],
    pathex=[root],
    datas=[(os.path.join(root, "app", "ui"), os.path.join("app", "ui"))],
    # uvicorn picks its loop, protocol and lifespan modules at run time
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "pytest"],
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: a JunkZero.app bundle (a folder app; single-file windowed apps are deprecated on macOS)
    with open(os.path.join(root, "app", "__init__.py"), encoding="utf-8") as f:
        version = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="JunkZero",
        console=False,
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="JunkZero", upx=False)
    app = BUNDLE(
        coll,
        name="JunkZero.app",
        bundle_identifier="com.junkzero.app",
        version=version,
        info_plist={
            "CFBundleDisplayName": "JunkZero",
            "CFBundleShortVersionString": version,
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        },
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="JunkZero",
        console=False,  # windowed app: no console window behind the GUI
        upx=False,
    )
