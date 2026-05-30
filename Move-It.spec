# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('ui', 'ui'), ('Images', 'Images'), ('*.json', '.'), ('pose_landmarker_lite.task', '.'), ('pose_landmarker_heavy.task', '.'), ('pose_landmarker_full.task', '.')]
binaries = []
hiddenimports = ['pystray._win32']
tmp_ret = collect_all('mediapipe')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('webview')
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

app = BUNDLE(
    coll,
    name='Move-It.app',
    icon='icon.ico',
    bundle_identifier='com.ali.moveit',
    info_plist={
        'CFBundleName': 'Move-It',
        'CFBundleDisplayName': 'Move-It',
        'CFBundleExecutable': 'Move-It',
        'CFBundlePackageType': 'APPL',
        'CFBundleShortVersionString': '1.0.0',
        'LSMinimumSystemVersion': '10.13.0',
        'NSCameraUsageDescription': 'This app requires camera access for pose detection.',
        'NSMicrophoneUsageDescription': 'This app requires microphone access for audio functionality.'
    },
)
