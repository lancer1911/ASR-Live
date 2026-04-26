"""
downloader.py — 首次启动检测：扫描本地模型，缺失时引导用户下载
通过 result_q 向主进程发送进度，支持取消
"""
import os, re, subprocess, sys, time
from pathlib import Path
from multiprocessing import Process, Queue

HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

# 推荐模型（repo_id → 显示名 + 类型）
RECOMMENDED = {
    "mlx-community/whisper-large-v3-turbo": {
        "label": "Whisper large-v3-turbo",
        "type":  "whisper",
        "size":  "~3 GB",
        "desc":  "ASR 识别模型，支持中英日等99种语言",
    },
    "mlx-community/Qwen3-14B-4bit": {
        "label": "Qwen3-14B (4bit)",
        "type":  "llm",
        "size":  "~8 GB",
        "desc":  "LLM 矫正与翻译模型",
    },
}


def _repo_to_dir(repo_id: str) -> Path:
    """把 repo_id 转换为 HF 缓存目录名"""
    safe = repo_id.replace("/", "--")
    return HF_CACHE / f"models--{safe}"


def is_model_cached(repo_id: str) -> bool:
    """检查模型是否已完整下载到本地缓存"""
    model_dir = _repo_to_dir(repo_id)
    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return False
    for snap in snapshots.iterdir():
        if snap.is_dir():
            weights = list(snap.glob("*.safetensors")) + list(snap.glob("*.bin")) + list(snap.glob("*.npz"))
            if weights:
                return True
    return False


def scan_missing(repos: list[str]) -> list[str]:
    """返回尚未下载的模型列表"""
    return [r for r in repos if not is_model_cached(r)]


def download_model(repo_id: str, result_q: Queue) -> bool:
    """
    调用 hf CLI 下载模型，实时把进度发给 result_q。
    返回 True 表示成功。
    """
    result_q.put({"type": "dl_start", "repo": repo_id})

    # 临时开启联网（下载时需要）
    env = os.environ.copy()
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)

    cmd = ["hf", "download", repo_id]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, bufsize=1,
        )
        for line in proc.stdout:
            line = line.strip()
            if line:
                result_q.put({"type": "dl_progress", "repo": repo_id, "line": line})
        proc.wait()
        success = proc.returncode == 0
        result_q.put({"type": "dl_done", "repo": repo_id, "success": success})
        return success
    except FileNotFoundError:
        result_q.put({"type": "dl_error", "repo": repo_id,
                       "msg": "找不到 hf 命令，请先运行: pip install huggingface_hub"})
        return False
    except Exception as e:
        result_q.put({"type": "dl_error", "repo": repo_id, "msg": str(e)})
        return False
