"""
打包成 macOS .app（需要 Python 3.11 + py2app）

步骤：
  ~/.pyenv/versions/3.11.9/bin/python -m venv venv_build
  source venv_build/bin/activate
  pip install py2app
  rm -rf build dist
  python build_mac.py py2app
"""
from setuptools import setup

setup(
    app=["launcher.py"],
    name="ASR Live",
    data_files=[
        ("static", ["static/index.html"]),
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
            "CFBundleVersion":            "3.0.0",
            "CFBundleShortVersionString": "3.0",
            "NSMicrophoneUsageDescription":    "需要麦克风权限以进行实时语音识别",
            "NSLocalNetworkUsageDescription":  "本地服务器用于界面通信",
            "LSMinimumSystemVersion":     "13.0",
        },
        # launcher 本身只需标准库 + pywebview 相关
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
