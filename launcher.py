"""
launcher.py — Lancer1911 ASR Live .app 入口（纯标准库，无第三方依赖）
找到 ~/asr-env 的 Python 3.x，用它运行 main.py
"""
import os
import sys
import subprocess
from pathlib import Path


def find_python():
    """按优先级寻找 asr-env 的 Python"""
    candidates = [
        Path.home() / "asr-env" / "bin" / "python3",
        Path.home() / "asr-env" / "bin" / "python",
    ]
    # pyenv 版本（从新到旧）
    pyenv_root = Path.home() / ".pyenv" / "versions"
    if pyenv_root.exists():
        for v in sorted(pyenv_root.iterdir(), reverse=True):
            py = v / "bin" / "python3"
            if py.exists():
                candidates.append(py)
    candidates += [
        Path("/opt/homebrew/bin/python3"),
        Path("/usr/bin/python3"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def find_main():
    """找到 main.py：先看 Resources，再看同目录"""
    # 打包后在 Resources 目录
    here = Path(os.environ.get("RESOURCEPATH", ""))
    if here and (here / "main.py").exists():
        return str(here / "main.py")
    # 开发时同目录
    here = Path(__file__).resolve().parent
    if (here / "main.py").exists():
        return str(here / "main.py")
    return None


def alert(title, msg):
    script = f'display alert "{title}" message "{msg}" as critical'
    subprocess.run(["osascript", "-e", script], check=False)


def main():
    python = find_python()
    if not python:
        alert("Lancer1911 ASR Live - Missing environment",
              "Cannot find ~/asr-env.\nPlease complete the install steps in README.")
        sys.exit(1)

    main_py = find_main()
    if not main_py:
        alert("Lancer1911 ASR Live - File missing", "Cannot find main.py. Please reinstall.")
        sys.exit(1)

    work_dir = str(Path(main_py).parent)
    env = os.environ.copy()

    # 清除 py2app 注入的 Python 路径变量
    # 否则会导致 asr-env 的 Python 3.14 加载 .app 内的 3.11 标准库而崩溃
    for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONEXECUTABLE",
                "RESOURCEPATH", "EXECUTABLEPATH", "ARGVZERO"]:
        env.pop(key, None)

    env["HF_HUB_OFFLINE"]       = "1"
    env["TRANSFORMERS_OFFLINE"]  = "1"

    # 写日志到 ~/Library/Logs/ASRLive.log 方便调试
    log_path = Path.home() / "Library" / "Logs" / "ASRLive.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with open(log_path, "a", encoding="utf-8") as log:
        log.write(f"\n=== Lancer1911 ASR Live launch ===\n")
        log.write(f"Python: {python}\n")
        log.write(f"main.py: {main_py}\n")
        proc = subprocess.run(
            [python, main_py],
            cwd=work_dir,
            env=env,
            stdout=log,
            stderr=log,
        )
    sys.exit(proc.returncode)


if __name__ == "__main__":
    main()
