from pathlib import Path

from PyInstaller.utils.hooks import collect_all

root = Path(SPECPATH)
datas, binaries, hiddenimports = collect_all("playwright")
a = Analysis(
    [str(root / "src" / "office_music_bot" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="OfficeMusicEngine",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
