"""
downloader.py — 首次启动检测：扫描本地模型，缺失时引导用户下载
通过 result_q 向主进程发送进度，支持取消
"""
import os, re, subprocess, sys, time
from pathlib import Path
from multiprocessing import Process, Queue

HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

# 推荐模型（repo_id → 显示名 + 类型）
# "optional": True 表示可选（不阻断启动），False/缺省 表示必须
RECOMMENDED = {
    "mlx-community/whisper-large-v3-turbo": {
        "label":    "Whisper large-v3-turbo",
        "type":     "whisper",
        "size":     "~3 GB",
        "desc":     "ASR 识别模型（默认），支持中英日等99种语言",
        "optional": False,
    },
    "mlx-community/whisper-large-v3-mlx": {
        "label":    "Whisper large-v3",
        "type":     "whisper",
        "size":     "~3 GB",
        "desc":     "可选 Whisper 非 turbo 模型，速度较慢但更完整",
        "optional": True,
    },
    "mlx-community/SenseVoiceSmall": {
        "label":    "SenseVoiceSmall",
        "type":     "sensevoice",
        "size":     "~0.5 GB",
        "desc":     "可选 ASR 模型，速度更快，支持情绪/事件检测",
        "optional": True,
    },
    "mlx-community/Qwen3-14B-4bit": {
        "label":    "Qwen3-14B (4bit)",
        "type":     "llm",
        "size":     "~8 GB",
        "desc":     "LLM 矫正与翻译模型",
        "optional": False,
    },
    "mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit": {
        "label":    "Qwen3-30B-A3B-Instruct-2507 (4bit)",
        "type":     "llm",
        "size":     "~16 GB",
        "desc":     "可选高质量 LLM 矫正与翻译模型，需要更大统一内存",
        "optional": True,
    },
}


def _repo_to_dir(repo_id: str) -> Path:
    """把 repo_id 转换为 HF 缓存目录名"""
    safe = repo_id.replace("/", "--")
    return HF_CACHE / f"models--{safe}"


def is_model_cached(repo_id: str) -> bool:
    """检查模型是否已完整下载到本地缓存。

    修复 #9：仅凭权重文件存在不够——下载中断时文件可能已部分写入。
    额外验证 HuggingFace 缓存元数据：refs/main 指针必须存在且指向
    一个有权重文件的 snapshot 目录，才视为完整缓存。
    """
    model_dir = _repo_to_dir(repo_id)
    root_weights = (list(model_dir.glob("*.safetensors")) +
                    list(model_dir.glob("*.bin")) +
                    list(model_dir.glob("*.npz")))
    if root_weights:
        return True

    snapshots = model_dir / "snapshots"
    if not snapshots.exists():
        return False

    # ── 优先通过 refs/main 找到官方发布的 snapshot ──────────
    refs_main = model_dir / "refs" / "main"
    if refs_main.exists():
        try:
            commit_hash = refs_main.read_text(encoding="utf-8").strip()
            snap = snapshots / commit_hash
            if snap.is_dir():
                weights = (list(snap.glob("*.safetensors")) +
                           list(snap.glob("*.bin")) +
                           list(snap.glob("*.npz")))
                return bool(weights)
        except Exception:
            pass

    # ── 兜底：遍历所有 snapshot，任意一个有权重则认为可用 ───
    for snap in snapshots.iterdir():
        if snap.is_dir():
            weights = (list(snap.glob("*.safetensors")) +
                       list(snap.glob("*.bin")) +
                       list(snap.glob("*.npz")))
            if weights:
                return True

    return False


def scan_missing(repos: list[str]) -> list[str]:
    """返回尚未下载的模型列表"""
    return [r for r in repos if not is_model_cached(r)]


def download_model(repo_id: str, result_q: Queue) -> bool:
    """
    调用 hf CLI 下载模型，实时把进度发给 result_q。
    解析 hf download 输出中的百分比/文件数，广播为 dl_progress 消息。
    返回 True 表示成功。
    """
    result_q.put({"type": "dl_start", "repo": repo_id})

    # 临时开启联网（下载时需要）
    env = os.environ.copy()
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)

    # hf download 会把进度输出到 stderr（tqdm），stdout 输出最终路径
    cmd = ["hf", "download", repo_id]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            bufsize=1,
        )

        # 用两个线程并发读取 stdout/stderr，避免管道死锁
        import threading as _threading
        import queue as _queue

        _line_q = _queue.Queue()

        def _reader(stream, tag):
            try:
                for line in stream:
                    _line_q.put((tag, line))
            except Exception:
                pass
            finally:
                _line_q.put((tag, None))  # sentinel

        t_out = _threading.Thread(target=_reader, args=(proc.stdout, "out"), daemon=True)
        t_err = _threading.Thread(target=_reader, args=(proc.stderr, "err"), daemon=True)
        t_out.start(); t_err.start()

        done_tags = set()
        while len(done_tags) < 2:
            try:
                tag, line = _line_q.get(timeout=1.0)
            except _queue.Empty:
                continue
            if line is None:
                done_tags.add(tag)
                continue
            line = line.strip()
            if not line:
                continue

            # ── 从 tqdm / hf CLI 行提取进度百分比 ──────────────
            # 典型格式：
            #   "Downloading ...: 100%|████| 3.21G/3.21G [02:13<00:00, 24.1MB/s]"
            #   "Fetching 12 files:  83%|████▎   | 10/12 [00:01<00:00,  9.45it/s]"
            pct = None
            m_pct = re.search(r'(\d+)%\|', line)
            if m_pct:
                pct = int(m_pct.group(1))

            # 文件计数：10/12
            m_cnt = re.search(r'(\d+)/(\d+)', line)
            if m_cnt and pct is None:
                cur, tot = int(m_cnt.group(1)), int(m_cnt.group(2))
                if tot > 0:
                    pct = round(cur / tot * 100)

            # 去除 tqdm 控制字符，留下人可读文字
            clean = re.sub(r'[\x00-\x1f\x7f]', '', line)  # strip control chars
            clean = re.sub(r'\|[█▉▊▋▌▍▎▏ ]+\|', '', clean).strip()
            # 去掉过长的重复空白
            clean = re.sub(r'  +', ' ', clean)

            result_q.put({
                "type":  "dl_progress",
                "repo":  repo_id,
                "line":  clean or line,
                "pct":   pct,          # int 0-100 或 None
            })

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
