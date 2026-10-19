#!/bin/bash
# Makes the Linux downloads from the PyInstaller folder build (dist/JunkZero, built first with
# pyinstaller --noconfirm packaging/JunkZero.spec). Run from the repository root:
#   dist/JunkZero-Linux-x86_64.tar.gz  portable: unpack anywhere and run JunkZero/JunkZero
#   dist/junkzero_<version>_amd64.deb   installs to /opt/junkzero with a menu entry and the
#                                       junkzero command (MX Linux, Debian, Ubuntu, Mint)
set -euo pipefail
cd "$(dirname "$0")/../.."
test -x dist/JunkZero/JunkZero || { echo "Build dist/JunkZero first (pyinstaller packaging/JunkZero.spec)" >&2; exit 1; }
version=$(python3 -c "import app; print(app.__version__)")

tar -C dist -czf dist/JunkZero-Linux-x86_64.tar.gz JunkZero

root=$(mktemp -d)
trap 'rm -rf "$root"' EXIT
chmod 755 "$root"
mkdir -p "$root/opt" "$root/usr/bin" "$root/usr/share/applications" "$root/usr/share/icons/hicolor/256x256/apps" "$root/DEBIAN"
cp -a dist/JunkZero "$root/opt/junkzero"
ln -s /opt/junkzero/JunkZero "$root/usr/bin/junkzero"
install -m 644 packaging/linux/junkzero.desktop "$root/usr/share/applications/junkzero.desktop"
python3 - "$root/usr/share/icons/hicolor/256x256/apps/junkzero.png" <<'PY'
import sys
try:
    from PIL import Image
    Image.open("packaging/JunkZero.png").resize((256, 256), Image.LANCZOS).save(sys.argv[1])
except ImportError:  # without Pillow the full-size icon works too
    import shutil
    shutil.copy("packaging/JunkZero.png", sys.argv[1])
PY
install -m 755 packaging/linux/postrm "$root/DEBIAN/postrm"
size=$(du -sk --exclude=DEBIAN "$root" | cut -f1)
cat > "$root/DEBIAN/control" <<CONTROL
Package: junkzero
Version: $version
Section: utils
Priority: optional
Architecture: amd64
Maintainer: JunkZero <junkzero@users.noreply.github.com>
Installed-Size: $size
Depends: libc6 (>= 2.35), xdg-utils
Recommends: zenity | kdialog | yad, cron | systemd
Homepage: https://github.com/javajaga-usa/JunkZero
Description: Find and remove junk files safely
 JunkZero finds temp files, app and browser caches, leftover installers,
 build folders, broken downloads and duplicates, explains why each one is
 junk, and moves what you pick to the Trash so it can be restored.
CONTROL
dpkg-deb --build --root-owner-group "$root" "dist/junkzero_${version}_amd64.deb"
ls -l dist/JunkZero-Linux-x86_64.tar.gz "dist/junkzero_${version}_amd64.deb"
