"""
FastAPI 后端 v3 — 子进程隔离 MLX，避免 macOS 26 beta Metal/asyncio 冲突
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

# ─── 全局状态 ──────────────────────────────────────────────────
class State:
    def __init__(self):
        self.recording   = False
        self.settings    = load_settings()
        self.history: list[dict] = []
        self.ws_clients: list[WebSocket] = []
        self.asr_queue:  queue.Queue = queue.Queue()  # 原始音频 → ASR
        self.context:    list[str] = []
        self._stream:    Optional[sd.InputStream] = None
        self.worker:     Optional[ModelWorker] = None
        self._rec_chunks: list = []          # 录音原始 chunk 累积
        self._rec_path:   Optional[str] = None  # 当前录音文件路径
        self._save_thread = None                 # 保存 MP3 的线程引用
        self._downloading: bool = False          # 是否正在下载模型
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
        G._task_id += 1
        G._pending[G._task_id] = {"raw": raw, "lang": lang, "asr_ms": asr_ms}
        G.worker.send({
            "kind": "llm", "task_id": G._task_id,
            "prompt": prompt, "raw": raw, "lang": lang, "asr_ms": asr_ms,
        })
        G.context.append(raw)
        if len(G.context) > 24:
            G.context = G.context[-12:]

    elif t == "llm_done":
        resp   = msg.get("resp", "")
        llm_ms = msg.get("llm_ms", 0)
        raw    = msg.get("raw", "")
        lang   = msg.get("lang", "")
        asr_ms = msg.get("asr_ms", 0)

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

        entry = {
            "id":           int(time.time() * 1000),
            "raw":          raw,
            "corrected":    corrected,
            "language":     language,
            "translations": trans_cn,
            "asr_ms":       asr_ms,
            "llm_ms":       llm_ms,
            "timestamp":    time.strftime("%H:%M:%S"),
        }
        G.history.append(entry)
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
        audio = G.asr_queue.get()
        if audio is None:
            break
        if G.worker is None:
            continue
        _id += 1
        G.worker.send({
            "kind":          "asr",
            "task_id":       _id,
            "audio_bytes":   audio.tobytes(),
            "initial_prompt": G.settings.get("context_prompt") or None,
        })

# ─── VAD + 音频采集 ───────────────────────────────────────────
def _make_rec_path() -> str:
    """生成录音文件路径，保存在用户 Downloads 目录"""
    import os
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    return str(downloads / f"ASRLive_{ts}.mp3")


def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件，兼容 .app 环境"""
    import shutil
    # 先找 PATH 里的
    found = shutil.which("ffmpeg")
    if found:
        return found
    # .app 里 PATH 可能不含 Homebrew，手动查找
    for p in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]:
        if os.path.exists(p):
            return p
    return "ffmpeg"


def _save_mp3_async(chunks: list, path: str, bitrate: str = "192"):
    """在后台线程把 float32 PCM chunks 编码为 MP3（非 daemon，确保退出前完成）"""
    def _run():
        try:
            import subprocess
            audio = np.concatenate(chunks)
            pcm = (audio * 32767).clip(-32768, 32767).astype(np.int16)
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
            if proc.returncode == 0:
                size_mb = round(os.path.getsize(path) / 1024 / 1024, 1)
                print(f"[录音] 已保存：{path} ({size_mb}MB, {bitrate}kbps)", flush=True)
                broadcast_sync({"type": "rec_saved", "path": path, "size_mb": size_mb})
            else:
                err = proc.stderr.decode(errors="replace")
                print(f"[录音] ffmpeg 错误：{err[:200]}", flush=True)
        except Exception as e:
            print(f"[录音] 保存失败：{e}", flush=True)

    # 非 daemon 线程：主进程退出时会等待此线程完成
    t = threading.Thread(target=_run, daemon=False)
    t.start()
    return t


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

    # 初始化录音文件
    G._rec_chunks = []
    G._rec_path   = _make_rec_path()
    print(f"[录音] 开始录制 → {G._rec_path}", flush=True)

    def flush():
        if not buf: return
        audio = np.concatenate(buf)
        if len(audio)/16000 >= 0.25:
            G.asr_queue.put(audio)
        buf.clear(); sil_cnt[0]=0; speaking[0]=False; vad.reset_states()

    def cb(indata, frames, t_, status_):
        if not G.recording: return
        chunk = indata[:,0].copy().astype(np.float32)
        # 同步录音（仅在启用时累积，节省内存）
        if G.settings.get("save_recording", True):
            G._rec_chunks.append(chunk.copy())
        ev = vad(chunk, return_seconds=False)
        if ev:
            if "start" in ev: speaking[0]=True; sil_cnt[0]=0
            if "end"   in ev: flush(); return
        if speaking[0]:
            buf.append(chunk); sil_cnt[0]=0
            if len(buf)>=MAX_FR: flush()
        elif buf:
            sil_cnt[0]+=1; buf.append(chunk)
            if sil_cnt[0]>=SIL_TH: flush()

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
    # 保存录音（根据用户设置决定是否保存）
    if G._rec_chunks and G._rec_path:
        if G.settings.get("save_recording", True):
            bitrate = str(G.settings.get("mp3_bitrate", "192"))
            t = _save_mp3_async(list(G._rec_chunks), G._rec_path, bitrate)
            G._save_thread = t  # 保存引用，退出时等待
        else:
            print("[录音] 保存已关闭，丢弃录音数据", flush=True)
        G._rec_chunks = []
        G._rec_path   = None

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
            threading.Thread(target=start_audio, daemon=True).start()
            await _broadcast({"type":"recording","value":True})
        elif act == "stop":
            G.recording = False
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
            G.history.clear(); G.context.clear()
            await _broadcast({"type":"cleared"})
        elif act == "export":
            txt = _export(msg.get("format","txt"), msg.get("lang_filter","all"))
            await ws.send_json({"type":"export","format":msg.get("format"),"content":txt})

        elif act == "save_file":
            filename = msg.get("filename", "export.txt")
            file_content = msg.get("content", "")
            _save_file_dialog(filename, file_content, ws)

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
    h = G.history

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
