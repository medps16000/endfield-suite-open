# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_submodules

project_root = Path.cwd().resolve().parents[1]
uploader_root = project_root / "apps" / "uploader"
parser_root = project_root / "packages" / "parser_core"
uploader_core_root = project_root / "packages" / "uploader_core"
client_source = project_root.parent / "endfield-pcap" / "src"

for source_root in (uploader_root, parser_root, uploader_core_root):
    source_path = str(source_root)
    if source_path not in sys.path:
        sys.path.insert(0, source_path)

datas = [
    (str(project_root / "data" / "akedata"), "data/akedata"),
    (str(project_root / "data" / "local_static"), "data/local_static"),
    (str(project_root / "data" / "local_semantics"), "data/local_semantics"),
    (str(project_root / "data" / "local_tables"), "data/local_tables"),
    (str(project_root / "data" / "packet_semantics"), "data/packet_semantics"),
    (str(project_root / "data" / "public"), "data/public"),
    (str(uploader_root / "app" / "assets"), "app/assets"),
]

hiddenimports = collect_submodules("parser_core")
hiddenimports += collect_submodules("uploader_core")
hiddenimports += ["socksio"]


a = Analysis(
    [str(uploader_root / "app" / "main.py")],
    pathex=[str(uploader_root), str(parser_root), str(uploader_core_root), str(client_source)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # httpx treats any importable `zstandard` namespace as full zstd support.
    # The Windows build host can contain only backend_c.pyd without __init__.py,
    # which yields a decoder object with no ZstdDecompressor at runtime.
    excludes=["zstandard"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EndfieldLogsUploader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(uploader_root / "app" / "assets" / "logo.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EndfieldLogsUploader",
)
