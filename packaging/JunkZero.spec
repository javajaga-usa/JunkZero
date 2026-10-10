# PyInstaller spec for the single-file JunkZero.exe.
# Build from the repository root:  pyinstaller --noconfirm packaging/JunkZero.spec
import os

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
