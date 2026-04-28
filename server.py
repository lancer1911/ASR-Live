"""
FastAPI 后端 v4.0f — 子进程隔离 MLX，避免 macOS 26 beta Metal/asyncio 冲突
内存泄漏修复：_pending清理 / context截断 / asr_queue限深 / chunks显式释放 / ws死连接清理 / 子进程强制kill
"""
import asyncio, json, os, queue, re, threading, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# 离线模式：所有 huggingface_hub 调用跳过网络检查，直接使用本地缓存
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import numpy as np
import sounddevice as sd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from model_worker import ModelWorker
from downloader import is_model_cached, scan_missing, download_model, RECOMMENDED

# ─── HF 缓存扫描 ──────────────────────────────────────────────
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

def scan_local_models() -> dict:
    whisper_models, llm_models = [], []
    if not HF_CACHE.exists():
        return {"whisper": whisper_models, "llm": llm_models}
    for model_dir in sorted(HF_CACHE.iterdir()):
        if not model_dir.is_dir() or not model_dir.name.startswith("models--"):
            continue
        snapshots = model_dir / "snapshots"
        if not snapshots.exists():
            continue
        has_weights = any(
            f.suffix in (".safetensors", ".bin", ".npz")
            for snap in snapshots.iterdir() if snap.is_dir()
            for f in snap.iterdir() if f.is_file()
        )
        if not has_weights:
            continue
        parts = model_dir.name[len("models--"):].split("--", 1)
        if len(parts) != 2:
            continue
        repo_id    = f"{parts[0]}/{parts[1]}"
        name_lower = parts[1].lower()
        if "whisper" in name_lower:
            whisper_models.append(repo_id)
        elif any(k in name_lower for k in ["qwen","llama","gemma","mistral","phi","deepseek"]):
            llm_models.append(repo_id)
    return {"whisper": whisper_models, "llm": llm_models}

# ─── 默认设置 ─────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "whisper_repo":   "mlx-community/whisper-large-v3-turbo",
    "llm_repo":       "mlx-community/Qwen3-14B-4bit",
    "translate_to":   ["中文", "英文", "日文"],
    "translate_map":  {"中文":"Chinese","英文":"English","日文":"Japanese","韩文":"Korean",
                       "法文":"French","德文":"German","西班牙文":"Spanish"},
    "silence_s":      0.6,
    "vad_threshold":  0.45,
    "max_sentence_s": 20.0,
    "ctx_sentences":  6,
    "font_size":      15,
    "auto_scroll":    True,
    "input_device":   None,
    "context_prompt": "",   # 用户自定义场景/术语描述，空字符串表示不启用
    "save_recording": True,  # 是否保存录音 MP3
    "mp3_bitrate":    "192", # MP3 码率：64 / 128 / 192 / 320
    "asr_language":   None,  # Whisper 识别语言锁定：None=自动, "zh"/"en"/"ja"/...
    "mic_gain":       1.5,   # 麦克风软件增益倍数：1.0=原始, 1.5=默认, 最大4.0
}

SETTINGS_FILE = Path.home() / ".asrlive_settings.json"

def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            s.update(json.loads(SETTINGS_FILE.read_text()))
        except Exception:
            pass
    return s

def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2))

# ─── 分段录音配置 ─────────────────────────────────────────────
SEGMENT_SECONDS = 300          # 每 5 分钟自动切一段
SEGMENT_FRAMES  = SEGMENT_SECONDS * 16000   # 对应帧数（float32）
HISTORY_MEM_MAX = 200          # 内存中最多保留最近 N 条 history

# ─── 全局状态 ──────────────────────────────────────────────────
class State:
    def __init__(self):
        self.recording   = False
        self.settings    = load_settings()
        self.history: list[dict] = []        # 内存仅保留最近 HISTORY_MEM_MAX 条
        self.ws_clients: list[WebSocket] = []
        self.asr_queue:  queue.Queue = queue.Queue(maxsize=20)  # 修复内存泄漏：限制队列深度，防止音频数组无限积压
        self.context:    list[str] = []
        self._stream:    Optional[sd.InputStream] = None
        self.worker:     Optional[ModelWorker] = None
        # ── 分段录音 ──────────────────────────────────────────
        self._rec_chunks: list = []          # 当前段的 chunk 缓冲
        self._rec_frames: int  = 0           # 当前段已累积帧数
        self._rec_segments: list[str] = []   # 已落盘的分段 MP3 路径
        self._rec_session_dir: Optional[str] = None  # 临时目录
        self._rec_final_path:  Optional[str] = None  # 最终合并路径
        self._seg_lock = threading.Lock()    # 保护分段写盘
        self._save_thread = None             # 最终合并线程引用
        self._last_saved_mp3: Optional[str] = None  # 最后保存的 MP3 路径（供清空时删除）
        # ── history 持久化 ────────────────────────────────────
        self._history_file: Optional[str] = None   # JSONL 落盘文件
        self._history_lock = threading.Lock()
        # ── 其他 ─────────────────────────────────────────────
        self._downloading: bool = False
        self._paused:      bool = False
        self._task_id    = 0
        self._pending:   dict = {}  # task_id → asr 结果暂存

G = State()

# ─── WebSocket 广播 ───────────────────────────────────────────
_main_loop: Optional[asyncio.AbstractEventLoop] = None

def broadcast_sync(msg: dict):
    global _main_loop
    try:
        if _main_loop and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(msg), _main_loop)
    except Exception:
        pass

async def _broadcast(msg: dict):
    dead = []
    for ws in list(G.ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in G.ws_clients:
            G.ws_clients.remove(ws)

# ─── 结果接收线程（轮询子进程 result_q）────────────────────────
def result_receiver():
    """持续从 ModelWorker 子进程读取结果并广播"""
    while True:
        if G.worker is None:
            time.sleep(0.05)
            continue
        msg = G.worker.result_q.get()  # 阻塞等待
        if msg is None:
            break
        handle_worker_result(msg)

def handle_worker_result(msg: dict):
    t = msg.get("type")

    if t == "status":
        broadcast_sync(msg)

    elif t == "asr_done":
        raw    = msg.get("raw", "")
        lang   = msg.get("lang", "")
        asr_ms = msg.get("asr_ms", 0)
        tid    = msg.get("task_id")
        if not raw:
            return
        broadcast_sync({"type":"asr_partial","raw":raw,"lang":lang,"asr_ms":asr_ms})
        # 构建 LLM prompt 并发送
        ctx = list(G.context[-G.settings["ctx_sentences"]:])
        prompt = build_llm_prompt(raw, lang, ctx)
        audio_start_time = msg.get("audio_start_time")
        G._task_id += 1
        G._pending[G._task_id] = {"raw": raw, "lang": lang, "asr_ms": asr_ms, "audio_start_time": audio_start_time}
        G.worker.send({
            "kind": "llm", "task_id": G._task_id,
            "prompt": prompt, "raw": raw, "lang": lang, "asr_ms": asr_ms,
            "audio_start_time": audio_start_time,
        })
        # 修复内存泄漏：立即截断，避免 context 积压到 25 条才清理
        G.context.append(raw)
        if len(G.context) > 12:
            G.context = G.context[-12:]

    elif t == "llm_done":
        resp   = msg.get("resp", "")
        llm_ms = msg.get("llm_ms", 0)
        raw    = msg.get("raw", "")
        lang   = msg.get("lang", "")
        asr_ms = msg.get("asr_ms", 0)
        # 修复内存泄漏：清理已完成的 pending 任务
        tid = msg.get("task_id")
        G._pending.pop(tid, None)

        # 解析 JSON
        resp_clean = re.sub(r"<think>.*?</think>", "", resp, flags=re.DOTALL)
        text = re.sub(r"```json\s*|```", "", resp_clean).strip()
        out = {}
        try:
            out = json.loads(text)
        except Exception:
            # 支持嵌套结构：贪婪匹配最外层 { ... }
            m = re.search(r"\{.*\}", text, re.DOTALL)
            if m:
                try:
                    out = json.loads(m.group())
                except Exception:
                    pass
            if not out:
                print(f"[LLM] 解析失败，原始:\n{resp[:400]}", flush=True)

        corrected  = out.get("corrected", raw)
        language   = out.get("language", lang)
        trans_raw  = out.get("translations", {})
        # 防御：LLM 偶尔返回非 dict
        if not isinstance(trans_raw, dict):
            print(f"[LLM] translations 格式异常: {type(trans_raw)} = {trans_raw}", flush=True)
            trans_raw = {}
        if not trans_raw and out:
            # 有输出但没有翻译，打印调试信息
            print(f"[LLM] 无翻译，out={json.dumps(out, ensure_ascii=False)[:200]}", flush=True)
        rev_map    = {v: k for k, v in G.settings.get("translate_map", {}).items()}
        trans_cn   = {rev_map.get(k, k): v for k, v in trans_raw.items()
                      if isinstance(v, str)}

        # 用句子在录音中的实际起始时刻生成字幕时间戳
        # audio_start_time 是 epoch 秒，精确到采样帧级别
        audio_start_time = msg.get("audio_start_time")
        if audio_start_time:
            ts_struct = time.localtime(audio_start_time)
            subtitle_ts = time.strftime("%H:%M:%S", ts_struct)
            # 相对于录音开始的偏移秒数，直接供前端播放器对齐使用
            rec_start = getattr(G, "_rec_start_time", None)
            mp3_offset_s = round(audio_start_time - rec_start, 3) if rec_start else None
        else:
            subtitle_ts  = time.strftime("%H:%M:%S")  # 兜底
            mp3_offset_s = None

        entry = {
            "id":           int(time.time() * 1000),
            "raw":          raw,
            "corrected":    corrected,
            "language":     language,
            "translations": trans_cn,
            "asr_ms":       asr_ms,
            "llm_ms":       llm_ms,
            "timestamp":    subtitle_ts,
            "mp3_offset_s": mp3_offset_s,   # 句子在 MP3 里的精确起始秒（float）
        }
        _history_append(entry)
        broadcast_sync({"type": "entry", **entry})

# ASR 语言代码 → 英文名（用于排除同语言翻译）
ASR_LANG_EN = {
    "zh": "Chinese", "chinese": "Chinese",
    "en": "English", "english": "English",
    "ja": "Japanese", "japanese": "Japanese",
    "ko": "Korean",  "korean": "Korean",
    "fr": "French",  "french": "French",
    "de": "German",  "german": "German",
    "es": "Spanish", "spanish": "Spanish",
}

def build_llm_prompt(raw: str, lang: str, ctx: list) -> str:
    tmap      = G.settings.get("translate_map", {})
    langs_cn  = G.settings.get("translate_to", [])
    # 排除与识别语言相同的翻译目标
    orig_en   = ASR_LANG_EN.get(lang.lower(), "")
    langs_en  = [tmap.get(l, l) for l in langs_cn
                 if tmap.get(l, l).lower() != orig_en.lower()]
    ctx_str   = "\n".join(f"[{i+1}] {s}" for i, s in enumerate(ctx))
    trans_pairs = ", ".join(f'"{l}": "<translation>"' for l in langs_en)
    trans_spec  = f',\n  "translations": {{{trans_pairs}}}' if langs_en else ""

    ctx_prompt = G.settings.get("context_prompt", "").strip()
    domain_instruction = (
        f"Domain context and terminology reference:\n{ctx_prompt}\n"
        "Use the above to improve correction accuracy for domain-specific terms.\n"
        if ctx_prompt else ""
    )

    return (
        "<|im_start|>system\n"
        "You are a multilingual ASR post-processor. "
        "Fix homophones, punctuation, and recognition errors using context. "
        "Keep the original language unchanged. Reply ONLY with valid compact JSON. No explanation.\n"
        f"{domain_instruction}"
        "/no_think\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Context (recent sentences):\n{ctx_str or '(none)'}\n\n"
        f"Raw ASR ({lang}): \"{raw}\"\n\n"
        "Fill in this JSON and return it:\n"
        "{\n"
        "  \"corrected\": \"<corrected text, same language>\",\n"
        f"  \"language\": \"<Chinese|English|Japanese>\"{trans_spec}\n"
        "}\n"
        "<|im_end|>\n"
        "<|im_start|>assistant\n"
    )

# ─── ASR 音频队列处理线程 ─────────────────────────────────────
def asr_dispatcher():
    """从本地 asr_queue 取音频，发给子进程"""
    _id = 0
    while True:
        item = G.asr_queue.get()
        if item is None:
            break
        if G.worker is None:
            continue
        # item 是 (audio_array, audio_start_time) 元组
        audio, audio_start_time = item
        _id += 1
        G.worker.send({
            "kind":             "asr",
            "task_id":          _id,
            "audio_bytes":      audio.tobytes(),
            "audio_start_time": audio_start_time,
            "initial_prompt":   G.settings.get("context_prompt") or None,
            "asr_language":     G.settings.get("asr_language") or None,  # None=自动检测
        })

# ─── 辅助工具 ─────────────────────────────────────────────────
def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件，兼容 .app 环境"""
    import shutil
    found = shutil.which("ffmpeg")
    if found:
        return found
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return "ffmpeg"


def _chunks_to_mp3_sync(chunks: list, path: str, bitrate: str = "192") -> bool:
    """将 float32 PCM chunk 列表同步编码为 MP3，返回是否成功"""
    import subprocess
    try:
        audio = np.concatenate(chunks)
        pcm   = (audio * 32767).clip(-32768, 32767).astype(np.int16)
        ffmpeg = _find_ffmpeg()
        cmd = [
            ffmpeg, "-y",
            "-f", "s16le", "-ar", "16000", "-ac", "1",
            "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-b:a", f"{bitrate}k",
            path
        ]
        proc = subprocess.run(cmd, input=pcm.tobytes(),
                              capture_output=True, timeout=120)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace")
            print(f"[录音] ffmpeg 错误：{err[:200]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[录音] 编码分段失败：{e}", flush=True)
        return False


# ─── 分段写盘 ─────────────────────────────────────────────────
def _flush_segment():
    """把当前 _rec_chunks 编码为一个分段 MP3，清空缓冲，释放内存"""
    with G._seg_lock:
        if not G._rec_chunks or not G._rec_session_dir:
            return
        idx     = len(G._rec_segments)
        seg_path = os.path.join(G._rec_session_dir, f"seg_{idx:04d}.mp3")
        chunks   = list(G._rec_chunks)   # 拷贝引用，不拷贝数据
        G._rec_chunks = []               # 立即释放主列表引用
        G._rec_frames = 0

    bitrate = str(G.settings.get("mp3_bitrate", "192"))
    ok = _chunks_to_mp3_sync(chunks, seg_path, bitrate)
    # 修复内存泄漏：编码完成后显式释放 numpy 数组引用
    del chunks
    if ok:
        G._rec_segments.append(seg_path)
        dur_min = (idx + 1) * SEGMENT_SECONDS // 60
        print(f"[录音] 分段 {idx} 已落盘 → {seg_path}（共 {dur_min} 分钟）", flush=True)
        broadcast_sync({"type": "rec_segment", "index": idx, "path": seg_path})


def _merge_segments_and_cleanup(segments: list[str], final_path: str):
    """合并所有分段 MP3 → 最终文件，完成后删除临时目录"""
    import subprocess, shutil
    try:
        if not segments:
            return
        if len(segments) == 1:
            import shutil as _sh
            _sh.copy2(segments[0], final_path)
        else:
            # 用 ffmpeg concat demuxer 无损合并 MP3
            session_dir = os.path.dirname(segments[0])
            list_file   = os.path.join(session_dir, "concat_list.txt")
            with open(list_file, "w", encoding="utf-8") as f:
                for p in segments:
                    f.write(f"file '{p}'\n")
            ffmpeg = _find_ffmpeg()
            cmd = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0",
                "-i", list_file,
                "-c", "copy",
                final_path
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=300)
            if proc.returncode != 0:
                err = proc.stderr.decode(errors="replace")
                print(f"[录音] 合并失败：{err[:300]}", flush=True)
                return

        size_mb = round(os.path.getsize(final_path) / 1024 / 1024, 1)
        print(f"[录音] 已合并保存：{final_path} ({size_mb}MB)", flush=True)
        G._last_saved_mp3 = final_path
        broadcast_sync({"type": "rec_saved", "path": final_path, "size_mb": size_mb})

        # 删除临时目录
        try:
            shutil.rmtree(os.path.dirname(segments[0]), ignore_errors=True)
        except Exception:
            pass
    except Exception as e:
        print(f"[录音] 合并异常：{e}", flush=True)


# ─── history 落盘 ─────────────────────────────────────────────
def _history_init():
    """录音开始时初始化 history JSONL 落盘文件"""
    ts = time.strftime("%Y%m%d_%H%M%S")
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    G._history_file = str(downloads / f"ASRLive_{ts}_history.jsonl")


def _history_append(entry: dict):
    """追加一条 history，超出上限时把旧条目落盘并从内存移除"""
    with G._history_lock:
        G.history.append(entry)
        # 落盘旧条目：超出 HISTORY_MEM_MAX 时把头部批量写盘
        if len(G.history) > HISTORY_MEM_MAX and G._history_file:
            overflow = G.history[: len(G.history) - HISTORY_MEM_MAX]
            G.history = G.history[len(G.history) - HISTORY_MEM_MAX :]
            try:
                with open(G._history_file, "a", encoding="utf-8") as f:
                    for e in overflow:
                        f.write(json.dumps(e, ensure_ascii=False) + "\n")
            except Exception as ex:
                print(f"[history] 落盘失败：{ex}", flush=True)


def _history_load_all() -> list[dict]:
    """读取全量 history（磁盘 + 内存），用于导出"""
    rows = []
    if G._history_file and os.path.exists(G._history_file):
        try:
            with open(G._history_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
        except Exception:
            pass
    with G._history_lock:
        rows.extend(G.history)
    return rows


# ─── VAD + 音频采集 ───────────────────────────────────────────
def start_audio():
    from silero_vad import load_silero_vad, VADIterator
    s       = G.settings
    vad_m   = load_silero_vad(onnx=True)
    vad     = VADIterator(vad_m, threshold=s["vad_threshold"],
                          sampling_rate=16000,
                          min_silence_duration_ms=int(s["silence_s"]*1000))
    CHUNK   = 512
    SIL_TH  = max(1, round(s["silence_s"]*16000/CHUNK))
    MAX_FR  = round(s["max_sentence_s"]*16000/CHUNK)
    buf: list[np.ndarray] = []
    sil_cnt = [0]; speaking = [False]

    # 初始化分段录音
    ts = time.strftime("%Y%m%d_%H%M%S")
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    G._rec_final_path  = str(downloads / f"ASRLive_{ts}.mp3")
    G._rec_session_dir = str(downloads / f".asrlive_tmp_{ts}")
    os.makedirs(G._rec_session_dir, exist_ok=True)
    G._rec_chunks       = []
    G._rec_frames       = 0
    G._rec_segments     = []
    G._rec_start_time   = time.time()   # 录音启动的精确 epoch 时间
    G._rec_frames_total = 0             # 累计所有已收到的采样帧数
    _history_init()
    print(f"[录音] 开始分段录制，最终输出 → {G._rec_final_path}", flush=True)

    # 追踪当前 VAD 句子的起始帧（相对录音开始，单位：采样点）
    sentence_start_frames = [0]

    def flush_vad():
        if not buf: return
        audio = np.concatenate(buf)
        if len(audio)/16000 >= 0.25:
            # 句子开始时刻 = 录音启动时刻 + 句子起始帧 / 采样率
            audio_start_time = G._rec_start_time + sentence_start_frames[0] / 16000.0
            # 修复内存泄漏：队列满时丢弃最旧帧，防止音频数组积压
            try:
                G.asr_queue.put_nowait((audio, audio_start_time))
            except queue.Full:
                try:
                    G.asr_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    G.asr_queue.put_nowait((audio, audio_start_time))
                except queue.Full:
                    pass
        buf.clear(); sil_cnt[0]=0; speaking[0]=False; vad.reset_states()
        # 记录下一句的起始帧
        sentence_start_frames[0] = G._rec_frames_total

    def cb(indata, frames, t_, status_):
        if not G.recording: return
        chunk = indata[:,0].copy().astype(np.float32)
        # ── 软件增益（防止削波，硬限幅在 ±1.0）────────────────
        gain = float(G.settings.get("mic_gain", 1.5))
        if gain != 1.0:
            chunk = np.clip(chunk * gain, -1.0, 1.0)
        G._rec_frames_total += len(chunk)

        # ── 分段录音累积 ──────────────────────────────────────
        if G.settings.get("save_recording", True):
            G._rec_chunks.append(chunk)
            G._rec_frames += len(chunk)
            # 达到分段阈值时在后台线程写盘，不阻塞音频回调
            if G._rec_frames >= SEGMENT_FRAMES:
                threading.Thread(target=_flush_segment, daemon=True).start()

        # ── VAD ──────────────────────────────────────────────
        ev = vad(chunk, return_seconds=False)
        if ev:
            if "start" in ev: speaking[0]=True; sil_cnt[0]=0
            if "end"   in ev: flush_vad(); return
        if speaking[0]:
            buf.append(chunk); sil_cnt[0]=0
            if len(buf)>=MAX_FR: flush_vad()
        elif buf:
            sil_cnt[0]+=1; buf.append(chunk)
            if sil_cnt[0]>=SIL_TH: flush_vad()

    G._stream = sd.InputStream(samplerate=16000, blocksize=CHUNK,
                                channels=1, dtype="float32",
                                device=s.get("input_device"), callback=cb)
    G._stream.start()


def stop_audio():
    if G._stream:
        try:
            G._stream.stop()
            G._stream.close()
            time.sleep(0.3)   # 等待 PortAudio 完全释放设备
        except Exception:
            pass
        G._stream = None

    if not G.settings.get("save_recording", True):
        print("[录音] 保存已关闭，丢弃录音数据", flush=True)
        G._rec_chunks = []; G._rec_frames = 0
        G._rec_segments = []
        return

    # 把最后一段（不足 5 分钟的尾巴）也写盘
    if G._rec_chunks:
        _flush_segment()
    # 修复内存泄漏：写盘后确保引用被释放
    G._rec_chunks = []
    G._rec_frames = 0

    if G._rec_segments and G._rec_final_path:
        segments    = list(G._rec_segments)
        final_path  = G._rec_final_path
        G._rec_segments  = []
        G._rec_final_path = None
        # 非 daemon 线程：退出前等待合并完成
        t = threading.Thread(
            target=_merge_segments_and_cleanup,
            args=(segments, final_path),
            daemon=False
        )
        t.start()
        G._save_thread = t


def _resume_audio():
    """暂停后继续：复用当前 session 目录，重启麦克风流追加分段"""
    from silero_vad import load_silero_vad, VADIterator
    s       = G.settings
    vad_m   = load_silero_vad(onnx=True)
    vad     = VADIterator(vad_m, threshold=s["vad_threshold"],
                          sampling_rate=16000,
                          min_silence_duration_ms=int(s["silence_s"]*1000))
    CHUNK   = 512
    SIL_TH  = max(1, round(s["silence_s"]*16000/CHUNK))
    MAX_FR  = round(s["max_sentence_s"]*16000/CHUNK)
    buf: list[np.ndarray] = []
    sil_cnt = [0]; speaking = [False]

    # 继续使用现有 session，不重置 _rec_segments / _rec_final_path
    G._rec_chunks = []
    G._rec_frames = 0
    # _rec_start_time / _rec_frames_total 保持不变，继续累计
    print(f"[录音] 继续录制，session → {G._rec_session_dir}", flush=True)

    sentence_start_frames = [G._rec_frames_total]

    def flush_vad():
        if not buf: return
        audio = np.concatenate(buf)
        if len(audio)/16000 >= 0.25:
            audio_start_time = G._rec_start_time + sentence_start_frames[0] / 16000.0
            # 修复内存泄漏：队列满时丢弃最旧帧，防止音频数组积压
            try:
                G.asr_queue.put_nowait((audio, audio_start_time))
            except queue.Full:
                try:
                    G.asr_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    G.asr_queue.put_nowait((audio, audio_start_time))
                except queue.Full:
                    pass
        buf.clear(); sil_cnt[0]=0; speaking[0]=False; vad.reset_states()
        sentence_start_frames[0] = G._rec_frames_total

    def cb(indata, frames, t_, status_):
        if not G.recording or G._paused: return
        chunk = indata[:,0].copy().astype(np.float32)
        # ── 软件增益（防止削波，硬限幅在 ±1.0）────────────────
        gain = float(G.settings.get("mic_gain", 1.5))
        if gain != 1.0:
            chunk = np.clip(chunk * gain, -1.0, 1.0)
        G._rec_frames_total += len(chunk)
        if G.settings.get("save_recording", True):
            G._rec_chunks.append(chunk)
            G._rec_frames += len(chunk)
            if G._rec_frames >= SEGMENT_FRAMES:
                threading.Thread(target=_flush_segment, daemon=True).start()
        ev = vad(chunk, return_seconds=False)
        if ev:
            if "start" in ev: speaking[0]=True; sil_cnt[0]=0
            if "end"   in ev: flush_vad(); return
        if speaking[0]:
            buf.append(chunk); sil_cnt[0]=0
            if len(buf)>=MAX_FR: flush_vad()
        elif buf:
            sil_cnt[0]+=1; buf.append(chunk)
            if sil_cnt[0]>=SIL_TH: flush_vad()

    if G._stream:
        try: G._stream.start()
        except Exception: pass
    else:
        G._stream = sd.InputStream(samplerate=16000, blocksize=CHUNK,
                                    channels=1, dtype="float32",
                                    device=s.get("input_device"), callback=cb)
        G._stream.start()

# ─── FastAPI lifespan ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    # 启动子进程 ModelWorker
    G.worker = ModelWorker(
        G.settings["whisper_repo"],
        G.settings["llm_repo"],
    )
    # 结果接收线程
    threading.Thread(target=result_receiver, daemon=True).start()
    # ASR 分发线程
    threading.Thread(target=asr_dispatcher, daemon=True).start()

    yield

    stop_audio()
    # 等待 MP3 保存完成（最多 30 秒）
    if G._save_thread and G._save_thread.is_alive():
        print("[退出] 等待录音保存完成…", flush=True)
        G._save_thread.join(timeout=30)
    if G.worker:
        G.worker.stop()

# ─── FastAPI 应用 ─────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(title="ASR Live", lifespan=lifespan)

    static = Path(__file__).parent / "static"
    if static.exists():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/ping")
    def ping(): return {"ok": True}

    @app.get("/api/models")
    def api_models(): return JSONResponse(scan_local_models())

    @app.get("/api/check_models")
    def api_check_models():
        """返回推荐模型的本地缓存状态"""
        result = {}
        for repo_id, info in RECOMMENDED.items():
            result[repo_id] = {
                **info,
                "cached": is_model_cached(repo_id),
            }
        return JSONResponse(result)

    @app.post("/api/download/{repo_path:path}")
    async def api_download(repo_path: str):
        """触发指定模型的下载（在后台线程执行）"""
        if G._downloading:
            return JSONResponse({"ok": False, "msg": "已有下载任务进行中"})
        G._downloading = True
        def _do():
            try:
                import queue as Q
                q = Q.Queue()
                def relay():
                    while True:
                        msg = q.get()
                        if msg is None:
                            break
                        broadcast_sync(msg)
                import threading
                t = threading.Thread(target=relay, daemon=True)
                t.start()
                download_model(repo_path, q)
                q.put(None)
                t.join()
            finally:
                G._downloading = False
        threading.Thread(target=_do, daemon=True).start()
        return JSONResponse({"ok": True})

    @app.get("/api/devices")
    def api_devices():
        import sounddevice as sd
        import ctypes
        # 强制 PortAudio 重新扫描设备（捕获新连接的蓝牙/USB设备）
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        devs = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                devs.append({"id": i, "name": d["name"]})
        return JSONResponse(devs)

    @app.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse((static / "index.html").read_text(encoding="utf-8"))

    @app.websocket("/ws")
    async def ws_ep(ws: WebSocket):
        await ws.accept()
        # 修复内存泄漏：新连接加入前清理已断开的死连接
        G.ws_clients[:] = [c for c in G.ws_clients if not c.client_state.value > 1]
        G.ws_clients.append(ws)
        await ws.send_json({
            "type":      "init",
            "recording": G.recording,
            "settings":  G.settings,
            "history":   G.history[-60:],
        })
        try:
            while True:
                msg = await ws.receive_json()
                await _handle(msg, ws)
        except WebSocketDisconnect:
            if ws in G.ws_clients: G.ws_clients.remove(ws)

    async def _handle(msg: dict, ws: WebSocket):
        act = msg.get("action")
        if act == "start" and not G.recording:
            G.recording = True
            G._paused   = False
            threading.Thread(target=start_audio, daemon=True).start()
            # start_audio 在新线程里赋值 _rec_start_time，稍等一下再读
            import time as _t
            for _ in range(20):
                if getattr(G, "_rec_start_time", None):
                    break
                _t.sleep(0.03)
            await _broadcast({
                "type":           "recording",
                "value":          True,
                "rec_start_time": getattr(G, "_rec_start_time", None),
            })
        elif act == "pause" and G.recording and not getattr(G, "_paused", False):
            G._paused = True
            # 暂停：停止麦克风流但保持分段状态
            if G._stream:
                try: G._stream.stop()
                except Exception: pass
            await _broadcast({"type":"recording","value":False})
        elif act == "resume" and getattr(G, "_paused", False):
            G._paused = False
            # 继续：重启麦克风流，复用当前 session 目录继续追加分段
            threading.Thread(target=_resume_audio, daemon=True).start()
            await _broadcast({"type":"recording","value":True})
        elif act == "stop":
            G.recording = False
            G._paused   = False
            stop_audio()
            await _broadcast({"type":"recording","value":False})
        elif act == "update_settings":
            G.settings.update(msg.get("settings", {}))
            save_settings(G.settings)
            if G.recording:
                stop_audio()
                threading.Thread(target=start_audio, daemon=True).start()
            await _broadcast({"type":"settings","settings":G.settings})
        elif act == "clear":
            with G._history_lock:
                G.history.clear()
            # 清空磁盘 history 文件
            if G._history_file and os.path.exists(G._history_file):
                try: os.remove(G._history_file)
                except Exception: pass
            G.context.clear()
            # 清空临时分段目录
            if G._rec_session_dir and os.path.isdir(G._rec_session_dir):
                try:
                    import shutil
                    shutil.rmtree(G._rec_session_dir, ignore_errors=True)
                except Exception: pass
                G._rec_session_dir = None
                G._rec_segments = []
            # 删除已合并的最终 MP3（仅在停止状态下存在）
            _last = getattr(G, "_last_saved_mp3", None)
            if _last and os.path.exists(_last):
                try: os.remove(_last)
                except Exception: pass
            G._last_saved_mp3 = None
            G._rec_chunks = []; G._rec_frames = 0
            await _broadcast({"type":"cleared"})
        elif act == "export":
            txt = _export(msg.get("format","txt"), msg.get("lang_filter","all"))
            await ws.send_json({"type":"export","format":msg.get("format"),"content":txt})

        elif act == "save_file":
            filename = msg.get("filename", "export.txt")
            file_content = msg.get("content", "")
            _save_file_dialog(filename, file_content, ws)

    # ── 提供本地录音文件给前端播放 ─────────────────────────────
    @app.get("/rec_file")
    def rec_file(path: str):
        from fastapi.responses import FileResponse
        p = Path(path)
        if not p.exists() or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        # 安全检查：只允许 Downloads 目录下的 mp3
        try:
            p.relative_to(Path.home() / "Downloads")
        except ValueError:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return FileResponse(str(p), media_type="audio/mpeg")

    return app

# ─── 导出 ─────────────────────────────────────────────────────
# 语言中文名 → ASR language 字段值（用于判断原文语言）
LANG_CN_TO_DETECTED = {
    "中文": ["chinese", "zh"],
    "英文": ["english", "en"],
    "日文": ["japanese", "ja"],
    "韩文": ["korean", "ko"],
    "法文": ["french", "fr"],
    "德文": ["german", "de"],
    "西班牙文": ["spanish", "es"],
}

def _save_file_dialog(filename: str, file_content: str, ws):
    """用 pywebview 原生保存对话框，或 fallback 到 Downloads 目录"""
    def _run():
        try:
            import webview as _wv
            wins = _wv.windows
            if wins:
                save_path = wins[0].create_file_dialog(
                    _wv.SAVE_DIALOG,
                    directory=str(Path.home() / "Downloads"),
                    save_filename=filename,
                )
                if save_path:
                    # create_file_dialog 返回 tuple 或 str
                    p = save_path[0] if isinstance(save_path, (list, tuple)) else save_path
                    Path(p).write_text(file_content, encoding="utf-8")
                    broadcast_sync({"type": "save_done", "path": p})
                else:
                    broadcast_sync({"type": "save_cancelled"})
                return
        except Exception as e:
            print(f"[保存] pywebview 对话框失败: {e}", flush=True)

        # fallback：直接写到 Downloads
        p = str(Path.home() / "Downloads" / filename)
        Path(p).write_text(file_content, encoding="utf-8")
        broadcast_sync({"type": "save_done", "path": p})

    threading.Thread(target=_run, daemon=True).start()


def _get_text_for_lang(e: dict, lang_filter: str) -> Optional[str]:
    """
    返回条目在指定语言下的文字：
    - 如果该条目的原文就是 lang_filter 对应语言，返回矫正后原文
    - 如果该条目有 lang_filter 的译文，返回译文
    - 否则返回 None（跳过）
    """
    detected = (e.get("language") or "").lower()
    codes = LANG_CN_TO_DETECTED.get(lang_filter, [])

    # 原文就是该语言
    if any(detected == c for c in codes):
        return e.get("corrected", "")

    # 有对应译文
    t = e.get("translations", {}).get(lang_filter)
    if t:
        return t

    return None


def _get_text(e: dict, lang_filter: str) -> Optional[str]:
    """根据语言筛选返回对应文字，None 表示跳过该条目"""
    if lang_filter == "all":
        return None  # 调用方自行组合
    if lang_filter == "corrected":
        return e.get("corrected", "")
    if lang_filter == "raw":
        return e.get("raw", "")
    # 指定语言：原文或译文均包含
    return _get_text_for_lang(e, lang_filter)


def _export(fmt: str, lang_filter: str = "all") -> str:
    # 导出时读取磁盘 + 内存的全量 history
    h = _history_load_all()
    if fmt == "txt":
        out = []
        for e in h:
            if lang_filter == "all":
                out.append(f"[{e['timestamp']}] [{e['language']}]")
                out.append(e.get("corrected", ""))
                for lang, text in e.get("translations", {}).items():
                    out.append(f"  → {lang}：{text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                out.append(f"[{e['timestamp']}] {t}")
            out.append("")
        return "\n".join(out)

    if fmt == "srt":
        out = []
        idx = 1
        for e in h:
            ts = e["timestamp"]
            if lang_filter == "all":
                lines = [e.get("corrected","")]
                for lang, text in e.get("translations",{}).items():
                    lines.append(f"[{lang}] {text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                lines = [t]
            out += [str(idx), f"{ts},000 --> {ts},000"] + lines + [""]
            idx += 1
        return "\n".join(out)

    if fmt == "json":
        if lang_filter == "all":
            return json.dumps(h, ensure_ascii=False, indent=2)
        filtered = []
        for e in h:
            t = _get_text(e, lang_filter)
            if t:
                filtered.append({
                    "timestamp": e["timestamp"],
                    "language":  lang_filter,
                    "text":      t,
                })
        return json.dumps(filtered, ensure_ascii=False, indent=2)

    if fmt == "md":
        out = ["# ASR Live 字幕记录\n"]
        for e in h:
            if lang_filter == "all":
                out.append(f"**[{e['timestamp']}]** `{e['language']}`\n")
                out.append(f"> {e.get('corrected','')}\n")
                for lang, text in e.get("translations",{}).items():
                    out.append(f"- **{lang}**：{text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                out.append(f"**[{e['timestamp']}]** {t}\n")
            out.append("")
        return "\n".join(out)

    return ""
