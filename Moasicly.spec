# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

import ortools


project_root = Path.cwd()
ortools_lib_dir = Path(ortools.__file__).resolve().parent / ".libs"
datas = [
    (str(project_root / "class_balancer" / "ui" / "qml" / "Main.qml"), "class_balancer/ui/qml"),
    (str(project_root / "mosaiclyIcon.ico"), "."),
    (str(project_root / "moasiclyIcon.png"), "."),
    (str(project_root / "AppIcon.ico"), "."),
    (str(project_root / "AppIcon.png"), "."),
]
binaries = [
    (str(ortools_lib_dir / "ortools.dll"), "."),
    (str(ortools_lib_dir / "abseil_dll.dll"), "."),
    (str(ortools_lib_dir / "libprotobuf.dll"), "."),
]

a = Analysis(
    ["class_balancer/__main__.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[
        "openpyxl",
        "ortools.sat.python.cp_model",
        "rapidfuzz",
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
    name="Moasicly",
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
    icon=str(project_root / "mosaiclyIcon.ico"),
)
