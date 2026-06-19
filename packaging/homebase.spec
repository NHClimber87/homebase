# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — builds a single self-contained HomeBase.exe (no separate runtime).
#
# Build (on Windows, in a venv with pyinstaller + tzdata):
#     pip install pyinstaller tzdata
#     pyinstaller packaging/homebase.spec
# Output: dist/HomeBase.exe
#
# tzdata is bundled because Windows has no system IANA tz database, and AC-CORR-1
# (DST-correct America/New_York) depends on it. The static front-end and the bundled
# data assets (NYSE calendar, symbol map) are collected so the __file__-relative reads
# resolve inside the onefile bundle.

from PyInstaller.utils.hooks import collect_data_files, collect_all

datas = []
datas += collect_data_files("homebase", includes=["static/*", "markets/*.json"])
tz_datas, tz_binaries, tz_hidden = collect_all("tzdata")
datas += tz_datas

block_cipher = None

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=tz_binaries,
    datas=datas,
    hiddenimports=tz_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "freezegun", "numpy", "pandas"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="HomeBase",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # a small console window shows the URL + "Press Ctrl+C to stop"
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
