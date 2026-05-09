# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=['win32com', 'win32com.client', 'win32com.server', 'win32com.server.util', 'win32com.shell', 'win32com.shell.shell', 'pythoncom', 'pywintypes', 'win32api', 'win32con', 'win32gui', 'openpyxl', 'openpyxl.styles', 'openpyxl.utils', 'openpyxl.utils.dataframe', 'openpyxl.styles.differential', 'openpyxl.styles.numbers'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'pandas', 'PIL', 'cv2', 'PyQt5', 'PySide2', 'test'],
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
    name='POAutomation',
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
