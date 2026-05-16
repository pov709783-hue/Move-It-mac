# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

datas = [('ui', 'ui'), ('Images', 'Images'), ('*.json', '.'), ('pose_landmarker_lite.task', '.'), ('pose_landmarker_heavy.task', '.'), ('pose_landmarker_full.task', '.')]
binaries = []
if sys.platform == 'win32':
    binaries = [('.venv\\\\Library\\\\bin\\\\*.dll', '.')]
hiddenimports = []
tmp_ret = collect_all('mediapipe')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
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
    [],
    exclude_binaries=True,
    name='Move-It',
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
    icon=['icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Move-It',
)

if sys.platform == 'darwin':
    app = BUNDLE(
        coll,
        name='Move-It.app',
        icon=None,
        bundle_identifier='com.moveit.app',
    )
