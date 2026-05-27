# Lights_Out.spec
# Build with:  uvx pyinstaller Lights_Out.spec

from PyInstaller.building.build_main import Analysis, PYZ, EXE

a = Analysis(
    ['Lights_Out.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('power.ico', '.'),   # bundle the icon so the tray can load it at runtime
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='Lights Out',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='power.ico',       # taskbar / explorer icon
    uac_admin=False,        # does not require elevation
    onefile=True,           # single .exe output
)
