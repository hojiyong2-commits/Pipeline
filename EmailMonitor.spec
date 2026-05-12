# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

fitz_datas, fitz_binaries, fitz_hiddenimports = collect_all('fitz')
pdfplumber_datas, pdfplumber_binaries, pdfplumber_hiddenimports = collect_all('pdfplumber')

a = Analysis(
    ['po_automation\\main.py'],
    pathex=[],
    binaries=fitz_binaries + pdfplumber_binaries,
    datas=fitz_datas + pdfplumber_datas,
    hiddenimports=fitz_hiddenimports + pdfplumber_hiddenimports + [
        'win32com', 'win32com.client', 'win32com.server', 'win32timezone',
        'openpyxl', 'openpyxl.styles', 'openpyxl.utils',
    ],
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
    name='emailmonitor',
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
