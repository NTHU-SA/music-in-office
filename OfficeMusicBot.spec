from pathlib import Path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "src" / "office_music_bot" / "__main__.py")],
    pathex=[str(root / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    excludes=["pytest", "ruff", "playwright", "tkinter"],
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
