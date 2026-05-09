# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['c:\\Users\\hojiy\\OneDrive\\Desktop\\Projects\\Really good agents for QA and Orchestra\\ic_part_src\\main.py'],
    pathex=['c:\\Users\\hojiy\\OneDrive\\Desktop\\Projects\\Really good agents for QA and Orchestra\\ic_part_src'],
    binaries=[],
    datas=[],
    hiddenimports=['win32com', 'win32com.client', 'pywintypes', 'win32api', 'win32con', 'watchdog.observers', 'watchdog.observers.winapi', 'watchdog.events'],
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
    name='ICPartAutomation',
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
)
