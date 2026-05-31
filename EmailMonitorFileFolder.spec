# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['po_automation/file_email_processor_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'extract_msg',
        'extract_msg.msg_classes',
        'extract_msg.attachments',
        'fitz',
        'pdfplumber',
        'PIL',
        'PIL.Image',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'cv2',
        'PyQt5',
        'PySide2',
        'test',
        'win32com',
        'pythoncom',
        'pywintypes',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='emailmonitor_file',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='emailmonitor_file_folder',
)
