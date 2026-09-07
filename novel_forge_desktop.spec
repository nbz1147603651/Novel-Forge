# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file for NIMO Desktop
Usage:
    pyinstaller novel_forge_desktop.spec
Or use the convenience script:
    python scripts/build_pyside_desktop.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH injected by PyInstaller)

# ---------------------------------------------------------------------------
# Data files: (source, dest_dir_in_bundle)
# ---------------------------------------------------------------------------
datas = [
    # Jinja2 prompt templates
    (str(ROOT / "novel_forge" / "prompts" / "prompts"), "novel_forge/prompts/prompts"),
    # Qt stylesheet resources (SVG arrows)
    (str(ROOT / "novel_forge" / "desktop" / "resources"), "novel_forge/desktop/resources"),
]

# Managed sidecars run under their own CPython, so they cannot import modules
# stored only inside PyInstaller's PYZ archive.  Ship the narrow importable
# source surface used by those child processes instead of duplicating the
# complete desktop package and prompt library.
_sidecar_runtime_sources = (
    "novel_forge/__init__.py",
    "novel_forge/tts/__init__.py",
    "novel_forge/tts/audio_analysis_sidecar.py",
    "novel_forge/tts/qwen3_sidecar.py",
    "novel_forge/tts/reference_voice_store.py",
    "novel_forge/tts/sherpa_onnx_sidecar.py",
    "novel_forge/tts/whisperx_sidecar.py",
    "novel_forge/persistence/__init__.py",
    "novel_forge/persistence/base.py",
    "novel_forge/persistence/filesystem.py",
)
for _relative_source in _sidecar_runtime_sources:
    _source_path = ROOT / _relative_source
    datas.append(
        (
            str(_source_path),
            str(Path("runtime_support") / _source_path.relative_to(ROOT).parent),
        )
    )

_ollama_bundle_dir = os.environ.get("NOVEL_FORGE_OLLAMA_BUNDLE_DIR", "").strip()
_ollama_bundle_path = (
    Path(_ollama_bundle_dir).expanduser().resolve()
    if _ollama_bundle_dir
    else (ROOT / "vendor" / "ollama").resolve()
)
if _ollama_bundle_path.exists():
    datas.append((str(_ollama_bundle_path), "vendor/ollama"))

# Root-level config files are optional (user-generated / gitignored); only
# bundle them when they actually exist on the current build machine.
for _cfg in ("model_profiles.json", "long_preset.json", "novel_forge.cli.json"):
    _cfg_path = ROOT / _cfg
    if _cfg_path.exists():
        datas.append((str(_cfg_path), "."))

# ---------------------------------------------------------------------------
# Hidden imports: dynamically-loaded adapters and sub-packages
# ---------------------------------------------------------------------------
hidden_imports = [
    # Gateway adapters (loaded by string name in factory.py)
    "novel_forge.gateway.adapters.openai",
    "novel_forge.gateway.adapters.anthropic",
    "novel_forge.gateway.adapters.openai_compat",
    "novel_forge.gateway.adapters.deepseek",
    "novel_forge.gateway.adapters.kimi",
    "novel_forge.gateway.adapters.minimax",
    "novel_forge.gateway.adapters.ollama",
    "novel_forge.gateway.adapters.tongyi",
    "novel_forge.gateway.adapters.tencent_hunyuan",
    "novel_forge.gateway.adapters.mock",
    "novel_forge.gateway.secure_keys",
    # novel_forge.desktop modules loaded via importlib.import_module at runtime
    "novel_forge.desktop.theme",
    # PySide6 modules used via importlib.import_module at runtime
    "PySide6.QtWidgets",
    "PySide6.QtGui",
    "PySide6.QtCore",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    # Optional provider SDKs (may not be installed; PyInstaller will skip if absent)
    "openai",
    "anthropic",
    # Misc runtime deps
    "keyring",
    "psutil",
    "aiofiles",
    "jinja2",
    "uvicorn",
    "fastapi",
    "httpx",
    # Sound model downloads import huggingface_hub lazily so it must be kept
    # explicitly in the frozen desktop bundle.
    "huggingface_hub",
]

# ---------------------------------------------------------------------------
# Binaries: PySide6 plugins needed for SVG rendering
# ---------------------------------------------------------------------------
try:
    import PySide6
    _pyside_dir = Path(PySide6.__file__).parent
    _svg_plugin = _pyside_dir / "Qt" / "plugins" / "imageformats"
    # Extension is platform-dependent: .dylib (macOS), .dll (Windows), .so (Linux)
    _ext = {"darwin": "*.dylib", "win32": "*.dll"}.get(sys.platform, "*.so")
    binaries = []
    if _svg_plugin.exists():
        binaries.append((str(_svg_plugin / _ext), "PySide6/Qt/plugins/imageformats"))
except ImportError:
    binaries = []

_uv_bundle = os.environ.get("NOVEL_FORGE_UV_BUNDLE_PATH", "").strip()
if _uv_bundle:
    _uv_path = Path(_uv_bundle).expanduser().resolve()
    if not _uv_path.is_file():
        raise FileNotFoundError(f"uv bundle executable does not exist: {_uv_path}")
    binaries.append((str(_uv_path), "vendor/uv"))

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
a = Analysis(  # noqa: F821
    [str(ROOT / "novel_forge" / "desktop" / "main.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "numpy",
        "scipy",
        "pandas",
        "IPython",
        "jupyter",
        "notebook",
        "pytest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)  # noqa: F821

# ---------------------------------------------------------------------------
# macOS .app bundle (one-dir mode — faster startup, easier updates)
# ---------------------------------------------------------------------------
if sys.platform == "darwin":
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="NIMO",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=str(ROOT / "logo" / "nimo-logo.icns"),
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="NIMO",
    )
    app = BUNDLE(  # noqa: F821
        coll,
        name="NIMO.app",
        icon=str(ROOT / "logo" / "nimo-logo.icns"),
        bundle_identifier="com.nimo.desktop",
        info_plist={
            "CFBundleName": "NIMO",
            "CFBundleDisplayName": "NIMO",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
            "NSRequiresAquaSystemAppearance": False,
        },
    )

# ---------------------------------------------------------------------------
# Windows .exe (single-file mode)
# ---------------------------------------------------------------------------
elif sys.platform == "win32":
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="NIMO",
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
        icon=str(ROOT / "logo" / "nimo-logo.ico"),
        version_file=None,
    )

# ---------------------------------------------------------------------------
# Linux (one-dir mode)
# ---------------------------------------------------------------------------
else:
    exe = EXE(  # noqa: F821
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="NIMO",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        icon=str(ROOT / "logo" / "nimo-logo.png"),
    )
    coll = COLLECT(  # noqa: F821
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="NIMO",
    )
