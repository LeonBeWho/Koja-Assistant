# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

project_dir = Path.cwd()

# Piper binaries/models are intentionally not bundled by default because they are
# large and platform-specific. Keep ./piper and ./models next to the executable
# or adjust datas below if you want a heavier self-contained build.
datas = [
    (str(project_dir / "koja-core.py"), "."),
    (str(project_dir / "README.md"), "."),
    (str(project_dir / ".env.example"), "."),
]

hiddenimports = [
    "speech_recognition",
    "spotipy",
    "spotipy.oauth2",
    "llama_index.core",
    "llama_index.llms.ollama",
    "llama_index.embeddings.huggingface",
]


a = Analysis(
    ["koja_app.py"],
    pathex=[str(project_dir)],
    binaries=[],
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
    name="KojaCore",
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
    name="KojaCore",
)
