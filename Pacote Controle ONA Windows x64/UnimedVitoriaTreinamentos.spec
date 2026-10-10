# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

pydantic_core_datas, pydantic_core_binaries, pydantic_core_hiddenimports = collect_all('pydantic_core')
hiddenimports = collect_submodules('uvicorn') + pydantic_core_hiddenimports


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=pydantic_core_binaries,
    datas=[('static', 'static'), ('seed.json', '.')] + pydantic_core_datas,
    hiddenimports=hiddenimports,
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
    name='UnimedVitoriaTreinamentos',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['UnimedVitoria.ico'],
)
