"""
打包成 macOS .app（需要 Python 3.11 + py2app）

步骤：
  source ~/Playground/asr_app/venv_build/bin/activate
  cd ~/Playground/asr_app_v4_0
  python build_mac.py py2app

build / dist 输出到 ~/Playground/asr_app/
"""
import os, sys
from pathlib import Path
from setuptools import setup

# 固定输出目录，与源码目录分离，升级版本后无需清理
_OUT = Path.home() / "Playground" / "asr_app"
_OUT.mkdir(parents=True, exist_ok=True)

# py2app 不支持 build_base，用 --dist-dir 注入 sys.argv
dist_dir = str(_OUT / "dist")
if "py2app" in sys.argv and "--dist-dir" not in sys.argv:
    sys.argv += ["--dist-dir", dist_dir]

setup(
    app=["launcher.py"],
    name="ASR Live",
    data_files=[
        ("static", ["static/index.html", "static/i18n.js"]),
        ("",       ["main.py", "server.py", "model_worker.py",
                    "downloader.py", "requirements.txt"]),
    ],
    options={"py2app": {
        "argv_emulation": False,
        "iconfile":       "icon.icns",
        "plist": {
            "CFBundleName":               "ASR Live",
            "CFBundleDisplayName":        "ASR Live",
            "CFBundleIdentifier":         "com.local.asrlive",
            "CFBundleVersion":            "4.0.0",
            "CFBundleShortVersionString": "4.0",
            "NSMicrophoneUsageDescription":    "需要麦克风权限以进行实时语音识别",
            "NSLocalNetworkUsageDescription":  "本地服务器用于界面通信",
            "LSMinimumSystemVersion":     "13.0",
        },
        "packages": ["encodings"],
        "includes": ["encodings", "encodings.utf_8", "encodings.ascii",
                     "encodings.latin_1", "os", "sys", "subprocess",
                     "pathlib"],
        "excludes": [
            "tkinter", "matplotlib", "test", "unittest",
            "PyQt5", "PyQt6", "wx",
            "mlx", "torch", "numpy", "scipy",
        ],
        "semi_standalone": False,
        "strip":           True,
    }},
    setup_requires=["py2app"],
)
