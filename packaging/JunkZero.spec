# PyInstaller spec for JunkZero on Windows (a folder build: dist/JunkZero/JunkZero.exe plus its
# files, which the Inno Setup installer and the portable zip are made from), JunkZero.app
# (macOS) and Linux (dist/JunkZero/JunkZero, which packaging/linux/build-linux.sh turns into a
# tar.gz and a .deb). Build from the repository root:  pyinstaller --noconfirm packaging/JunkZero.spec
import os
import re
import sys

from PyInstaller.utils.hooks import collect_submodules

root = os.path.dirname(SPECPATH)

with open(os.path.join(root, "app", "__init__.py"), encoding="utf-8") as f:
    version = re.search(r'__version__ = "([^"]+)"', f.read()).group(1)

a = Analysis(
    [os.path.join(SPECPATH, "junkzero.py")],
    pathex=[root],
    datas=[(os.path.join(root, "app", "ui"), os.path.join("app", "ui"))],
    # uvicorn picks its loop, protocol and lifespan modules at run time
    hiddenimports=collect_submodules("uvicorn"),
    # Linux: pywebview's window needs the system's GTK and WebKit Python bindings, which a
    # packaged app can't use, so the Linux build opens JunkZero in the web browser
    excludes=["tkinter", "pytest"] + (["webview"] if sys.platform.startswith("linux") else []),
)
pyz = PYZ(a.pure)

if sys.platform == "darwin":
    # macOS: a JunkZero.app bundle (a folder app; single-file windowed apps are deprecated on macOS)
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
        icon=os.path.join(SPECPATH, "JunkZero.icns"),
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
    version_info = None
    if sys.platform == "win32":  # the version resource needs pefile, which PyInstaller has on Windows only
        from PyInstaller.utils.win32.versioninfo import (
            FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo,
        )

        # File details shown in Explorer (Properties > Details): 1.2.0 -> (1, 2, 0, 0)
        numbers = tuple((list(int(n) for n in re.findall(r"\d+", version)[:4]) + [0, 0, 0, 0])[:4])
        version_info = VSVersionInfo(
            ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
            kids=[
                StringFileInfo([StringTable("040904B0", [
                    StringStruct("CompanyName", "JunkZero"),
                    StringStruct("FileDescription", "JunkZero - find and remove junk files safely"),
                    StringStruct("FileVersion", version),
                    StringStruct("InternalName", "JunkZero"),
                    StringStruct("OriginalFilename", "JunkZero.exe"),
                    StringStruct("ProductName", "JunkZero"),
                    StringStruct("ProductVersion", version),
                ])]),
                VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
            ],
        )
    # A folder build: starts faster than a single-file exe (nothing to unpack at every launch)
    # and is less often mistaken for malware by antivirus programs
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="JunkZero",
        icon=os.path.join(SPECPATH, "JunkZero.ico") if sys.platform == "win32" else None,
        version=version_info,
        console=False,  # windowed app: no console window behind the GUI
        upx=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, name="JunkZero", upx=False)
