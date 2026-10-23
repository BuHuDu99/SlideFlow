# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all


datas = [
    (str(Path("assets/SlideFlow.png").resolve()), "assets"),
    (str(Path("assets/SlideFlowToolbar.png").resolve()), "assets"),
]
binaries = []
hiddenimports = []
for package in ("customtkinter", "yt_dlp", "yt_dlp_ejs", "imageio_ffmpeg", "faster_whisper", "youtube_transcript_api"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

deno_executable = Path(sys.executable).with_name("deno")
if deno_executable.is_file():
    binaries.append((str(deno_executable), "."))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "scipy", "pytesseract", "reportlab", "skimage"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SlideFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    argv_emulation=False,
    target_arch="arm64",
)
collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SlideFlow",
)
app = BUNDLE(
    collection,
    name="SlideFlow.app",
    icon="assets/SlideFlow.icns",
    bundle_identifier="com.buihuuduc.slideflow",
    info_plist={
        "CFBundleDisplayName": "SlideFlow",
        "CFBundleShortVersionString": "2.7.3",
        "CFBundleVersion": "2.7.3",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": (
            "Uses open-source yt-dlp, FFmpeg, OpenCV, and CustomTkinter.\n\n"
            "Conceptualized and created by BuHuDu."
        ),
    },
)
