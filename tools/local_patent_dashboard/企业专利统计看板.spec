# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path

root = Path(SPECPATH)
ocr_models = root / "ocr" / "tessdata"
model_datas = [(str(ocr_models), "ocr/tessdata")] if ocr_models.is_dir() and os.environ.get("PATENT_EXTERNAL_OCR") != "1" else []

a = Analysis(
    [str(root / "local_dashboard.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "static"), "static"),
    ] + model_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="企业专利统计看板",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
