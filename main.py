"""
ASR Live v3 — 主入口
用法：
  浏览器模式：python main.py          (在浏览器访问 http://localhost:17433)
  原生窗口：  python main.py --window
  打包：      python build_mac.py py2app
"""
import sys, threading, time, urllib.request
import uvicorn
from server import create_app

PORT = 17433


def _free_port():
    """杀掉占用端口的旧进程"""
    import subprocess
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{PORT}"],
            capture_output=True, text=True
        )
        pids = result.stdout.strip().split()
        for pid in pids:
            subprocess.run(["kill", "-9", pid], check=False)
        if pids:
            time.sleep(0.3)
    except Exception:
        pass


def _start_server():
    uvicorn.run(create_app(), host="127.0.0.1", port=PORT, log_level="warning")


def main():
    # --browser 参数可强制用浏览器打开（调试用）
    use_browser = "--browser" in sys.argv

    _free_port()
    t = threading.Thread(target=_start_server, daemon=True)
    t.start()

    # 等服务就绪
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PORT}/ping", timeout=1)
            break
        except Exception:
            time.sleep(0.15)

    # 检查是否需要下载模型（首次使用引导）
    import json as _json, urllib.request as _req
    try:
        resp = _req.urlopen(f"http://127.0.0.1:{PORT}/api/check_models", timeout=3)
        models_status = _json.loads(resp.read())
        missing = [(repo, info) for repo, info in models_status.items() if not info["cached"]]
        if missing:
            print("\n" + "="*60)
            print("  ASR Live - First launch, models needed")
            print("="*60)
            for repo, info in missing:
                print(f"  - {info['label']} ({info['size']})")
            print("\n  The app will guide you through the download.")
            print("="*60 + "\n")
    except Exception:
        pass

    if use_browser:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        print(f"ASR Live: http://127.0.0.1:{PORT}")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        # 原生窗口模式（默认）
        # MLX 在独立子进程里，与 pywebview 的 Metal/WKWebView 完全隔离
        import webview

        window = webview.create_window(
            title="ASR Live",
            url=f"http://127.0.0.1:{PORT}",
            width=1300,
            height=840,
            min_size=(960, 640),
            background_color="#0d0f12",
            text_select=True,
        )
        webview.start(
            debug="--debug" in sys.argv,
            private_mode=False,
        )


if __name__ == "__main__":
    main()
