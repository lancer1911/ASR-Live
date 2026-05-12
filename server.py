"""
FastAPI 后端 v4.6a — 子进程隔离 MLX，避免 macOS 26 beta Metal/asyncio 冲突
内存泄漏修复：_pending清理 / context截断 / asr_queue限深 / chunks显式释放 / ws死连接清理 / 子进程强制kill
v4.2j 新增：编辑原文/重翻译，未保存提醒，自定义弹窗
v4.3a 新增：实时麦克风音量驱动波形图
v4.3b 新增：说话人日志（Speaker Diarization），最多4位发言人，支持重命名
v4.4d 修复：result_receiver竞态 / _task_id无锁自增 / update_settings重启状态错乱 /
           SRT时间戳 / _flush_threads泄漏 / _resume_audio静默失败 /
           SpeakerTracker锁外写属性 / LLM语言枚举不完整
v4.4g 修复：下载队列以 HTTP 完成作为推进信号；下载引导期间延迟启动模型子进程
v4.4h 修复：Whisper turbo / non-turbo 本地模型都可在设置页选择
v4.4i 调整：ASR 设置菜单文案改为“选择ASR家族 / 选择ASR模型”
v4.4j 修复：兼容 ModelScope 下载到 cache 根目录的 MLX LLM 模型
v4.4k 修复：ModelScope 根目录模型加载时传本地路径，避免离线 snapshot 查询失败
v4.4l 修复：切换模型后等待新 worker 就绪再允许开始识别
v4.4m 修复：LLM 翻译可靠性
v4.4n 修复：SpeakerTracker.identify() 四处逻辑错误
v4.4o 修复：说话人 warmup 默认改为 0
v4.4p 修复：短句发言人标记
v4.4q 修复：task_q限深+旧任务丢弃；context改用corrected；定期purge_pending；centroid加UPDATE_THRESHOLD — duration 阈值 0.5→0.2s；identify 返回 None 时回落到上一发言人，设置页新增预热时长滑块 — translations 展开为多行格式防截断；/no_think 移至 user 消息末尾；retranslate corrected 占位符明确化
v4.6a 修复：_flush_threads pause路径泄漏 / retranslate _pending持有ws引用 / _history_file未在stop时删除 / ws_clients定期全量清理
"""
import asyncio, collections, json, os, queue, re, threading, time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# 注意：不在主进程设置 HF_HUB_OFFLINE，因为 pyannote 需要通过本地缓存加载模型。
# Whisper/LLM 的离线标志在 model_worker.py 子进程中单独设置。

import numpy as np
import sounddevice as sd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from model_worker import ModelWorker
from downloader import is_model_cached, scan_missing, download_model, RECOMMENDED

# ─── HF 缓存扫描 ──────────────────────────────────────────────
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"

def _repo_to_cache_dir(repo_id: str) -> Path:
    return HF_CACHE / f"models--{repo_id.replace('/', '--')}"

def _has_root_weights(path: Path) -> bool:
    return any(path.glob("*.safetensors")) or any(path.glob("*.bin")) or any(path.glob("*.npz"))

def resolve_model_ref(model_ref: str) -> str:
    """Return a local path for ModelScope-style root-cache models, otherwise repo id."""
    if not model_ref or "/" not in model_ref or model_ref.startswith("/"):
        return model_ref
    cache_dir = _repo_to_cache_dir(model_ref)
    if cache_dir.is_dir() and _has_root_weights(cache_dir):
        return str(cache_dir)
    return model_ref

def scan_local_models() -> dict:
    whisper_models, llm_models, sensevoice_models = [], [], []
    if not HF_CACHE.exists():
        return {"whisper": whisper_models, "llm": llm_models, "sensevoice": sensevoice_models}

    for model_dir in sorted(HF_CACHE.iterdir()):
        if not model_dir.is_dir() or not model_dir.name.startswith("models--"):
            continue
        snapshots = model_dir / "snapshots"
        has_weights = _has_root_weights(model_dir) or (
            snapshots.exists() and any(
                f.suffix in (".safetensors", ".bin", ".npz")
                for snap in snapshots.iterdir() if snap.is_dir()
                for f in snap.iterdir() if f.is_file()
            )
        )
        if not has_weights:
            continue
        parts = model_dir.name[len("models--"):].split("--", 1)
        if len(parts) != 2:
            continue
        repo_id    = f"{parts[0]}/{parts[1]}"
        name_lower = parts[1].lower()
        if "sensevoice" in name_lower:
            sensevoice_models.append(repo_id)
        elif "whisper" in name_lower:
            whisper_models.append(repo_id)
        elif any(k in name_lower for k in ["qwen","llama","gemma","mistral","phi","deepseek"]):
            llm_models.append(repo_id)
    for repo_id, info in RECOMMENDED.items():
        if info.get("type") == "whisper" and is_model_cached(repo_id):
            whisper_models.append(repo_id)
        elif info.get("type") == "sensevoice" and is_model_cached(repo_id):
            sensevoice_models.append(repo_id)
        elif info.get("type") == "llm" and is_model_cached(repo_id):
            llm_models.append(repo_id)
    cur_whisper = G.settings.get("whisper_repo") if "G" in globals() else None
    cur_llm = G.settings.get("llm_repo") if "G" in globals() else None
    if cur_whisper and is_model_cached(cur_whisper):
        whisper_models.append(cur_whisper)
    if cur_llm and is_model_cached(cur_llm):
        llm_models.append(cur_llm)
    return {
        "whisper": sorted(set(whisper_models), key=lambda r: ("turbo" not in r.lower(), r.lower())),
        "llm": sorted(set(llm_models)),
        "sensevoice": sorted(set(sensevoice_models)),
    }


# ─── 说话人日志（Speaker Diarization）────────────────────────
# 使用 pyannote-audio 提取声纹 embedding，在线余弦相似度聚类
# 全部在主进程同步执行，不干扰 ASR 子进程

class SpeakerTracker:
    """
    在线说话人聚类：维护最多 MAX_SPEAKERS 个说话人的声纹中心向量。
    每个音频段提取 embedding 后，与已有中心向量比较余弦相似度；
    超过阈值则归为已知说话人，否则注册新说话人。
    """
    MAX_SPEAKERS    = 4
    MATCH_THRESHOLD = 0.68   # 余弦相似度 ≥ 此值视为同一人

    # 注册新说话人前，至少要积累这么多帧 embedding 取均值，避免冷启动误判
    MIN_FRAMES_TO_REGISTER = 2
    # 启动阶段先累计约 20 秒有效语音声纹，再开始给字幕输出发言人标签
    WARMUP_SECONDS = 20.0
    # 中心向量更新：前 N_CAP 帧用累积均值建立基础，之后用慢速 EMA 防止漂移
    N_CAP   = 8      # 累积均值的帧数上限
    EMA_ALPHA = 0.08  # EMA 学习率（越小越稳定，越难被单帧拉偏）
    UPDATE_THRESHOLD = 0.75  # 只有相似度超过此值才更新 centroid，防止误匹配污染声纹
    MIN_DUR_UPDATE   = 0.8   # 短于此时长的片段不更新 centroid（embedding 不够稳定）
    MIN_DUR_REGISTER = 0.5   # 短于此时长的片段不注册新说话人

    def __init__(self):
        self._lock        = threading.Lock()
        self._model       = None
        self._centroids: list = []
        self._counts:    list = []
        self._names:     dict = {}
        self._enabled     = False
        self._load_error  = None
        # 候选缓冲：还未正式注册的说话人的 embedding 积累
        # { candidate_key: [emb1, emb2, ...] }  candidate_key 从 0 开始
        self._candidates: dict = {}
        self._observed_s  = 0.0
        self._warmup_ready_logged = False
        # 启动时立即检测依赖包是否安装（不加载模型，几乎无耗时）
        self.packages_ok  = self._check_packages()

    @staticmethod
    def _check_packages() -> bool:
        """仅检查 import，不加载模型权重，毫秒级完成"""
        try:
            import importlib
            importlib.import_module("pyannote.audio")
            importlib.import_module("torch")
            return True
        except ImportError:
            return False

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if not self.packages_ok:
            return False
        if self._load_error:
            return False
        try:
            from pyannote.audio import Inference, Model
            print("[说话人] 正在加载 pyannote embedding 模型…", flush=True)
            model = Model.from_pretrained(
                "pyannote/embedding",
                use_auth_token=False,
            )
            self._model   = Inference(model, window="whole")
            self._enabled = True
            print("[说话人] 模型加载成功，说话人识别已启用 ✓", flush=True)
            # 通知所有已连接客户端状态变更
            broadcast_sync({"type": "speaker_status", "available": True, "active": True})
            return True
        except Exception as e:
            self._load_error = str(e)
            print(f"[说话人] 模型加载失败（已安装依赖但模型未缓存？）：{e}", flush=True)
            broadcast_sync({"type": "speaker_status", "available": False,
                            "active": False, "error": str(e)})
            return False

    def reset(self):
        with self._lock:
            self._centroids.clear()
            self._counts.clear()
            self._names.clear()
            self._candidates.clear()
            self._observed_s = 0.0
            self._warmup_ready_logged = False

    def set_name(self, speaker_id: int, name: str):
        with self._lock:
            self._names[speaker_id] = name.strip()

    def get_names(self) -> dict:
        with self._lock:
            return dict(self._names)

    @staticmethod
    def _cosine(a, b) -> float:
        na = np.linalg.norm(a); nb = np.linalg.norm(b)
        if na < 1e-9 or nb < 1e-9:
            return 0.0
        return float(np.dot(a, b) / (na * nb))

    def identify(self, audio, sr: int = 16000, threshold: float = None,
                 min_frames: int = None, warmup_s: float = None):
        if not self._ensure_model():
            return None
        match_threshold   = float(threshold)   if threshold   is not None else self.MATCH_THRESHOLD
        min_frames_to_reg = int(min_frames)     if min_frames  is not None else self.MIN_FRAMES_TO_REGISTER
        warmup_seconds    = float(warmup_s)     if warmup_s    is not None else self.WARMUP_SECONDS
        duration_s = len(audio) / sr
        if duration_s < 0.2:   # pyannote 仍可对 0.2s+ 的片段提取有效 embedding
            return None
        try:
            import torch
            audio_norm = audio.copy().astype(np.float32)
            peak = np.max(np.abs(audio_norm))
            if peak > 1e-6:
                audio_norm = audio_norm / peak
            rms = np.sqrt(np.mean(audio_norm ** 2))
            if rms > 1e-6:
                audio_norm = audio_norm * (0.1 / rms)
                audio_norm = np.clip(audio_norm, -1.0, 1.0)
            tensor = torch.from_numpy(audio_norm).unsqueeze(0)
            emb = self._model({"waveform": tensor, "sample_rate": sr})
            emb = np.array(emb).flatten()
            norm = np.linalg.norm(emb)
            if norm < 1e-3:
                print(f"[说话人] embedding 能量过低（norm={norm:.4f}），跳过", flush=True)
                return None
            emb = emb / norm
        except Exception as e:
            print(f"[说话人] embedding 提取失败：{e}", flush=True)
            return None

        with self._lock:
            self._observed_s += duration_s
            warmup_ready = self._observed_s >= warmup_seconds
            if warmup_ready and not self._warmup_ready_logged:
                print(f"[说话人] 已累计 {self._observed_s:.1f}s 有效语音，开始输出发言人判断", flush=True)
                self._warmup_ready_logged = True

            # ── 与已注册说话人比对 ─────────────────────────────
            best_idx, best_sim = -1, -1.0
            for i, c in enumerate(self._centroids):
                sim = self._cosine(emb, c)
                if sim > best_sim:
                    best_sim, best_idx = sim, i

            if best_idx >= 0:
                print(f"[说话人] 最高相似度={best_sim:.3f}（阈值={match_threshold}，发言人{best_idx+1}）", flush=True)

            if best_sim >= match_threshold:
                # ── 已知说话人：有条件更新中心向量 ──────────────────
                n = self._counts[best_idx]
                # 只在置信度足够高且音频时长够长时才更新 centroid
                # 避免短句或低置信度匹配污染已稳定的声纹
                if best_sim >= self.UPDATE_THRESHOLD and duration_s >= self.MIN_DUR_UPDATE:
                    if n < self.N_CAP:
                        self._centroids[best_idx] = (self._centroids[best_idx] * n + emb) / (n + 1)
                    else:
                        self._centroids[best_idx] = (1 - self.EMA_ALPHA) * self._centroids[best_idx] + self.EMA_ALPHA * emb
                    norm = np.linalg.norm(self._centroids[best_idx])
                    if norm > 1e-9:
                        self._centroids[best_idx] /= norm
                self._counts[best_idx] = n + 1
                # 候选缓冲中与此说话人相似的帧合并进来（不再单独注册）
                for ckey in list(self._candidates.keys()):
                    c_embs = self._candidates[ckey]
                    c_mean = np.mean(c_embs, axis=0)
                    c_mean /= (np.linalg.norm(c_mean) + 1e-9)
                    if self._cosine(c_mean, self._centroids[best_idx]) >= match_threshold:
                        del self._candidates[ckey]
                return best_idx if warmup_ready else None

            elif len(self._centroids) < self.MAX_SPEAKERS:
                # ── 未知说话人，进入候选缓冲 ──────────────────
                # 修复：积累期间返回 None 而不是错误归入已有说话人
                best_ckey, best_csim = None, -1.0
                for ckey, c_embs in self._candidates.items():
                    c_mean = np.mean(c_embs, axis=0)
                    c_mean /= (np.linalg.norm(c_mean) + 1e-9)
                    s = self._cosine(emb, c_mean)
                    if s > best_csim:
                        best_csim, best_ckey = s, ckey

                if best_csim >= match_threshold and best_ckey is not None:
                    self._candidates[best_ckey].append(emb)
                else:
                    # 修复：用单调递增计数器作 key，避免删除后 key 复用
                    new_key = max(self._candidates.keys(), default=-1) + 1
                    self._candidates[new_key] = [emb]
                    best_ckey = new_key

                # 修复：候选槽上限在锁内处理，避免代码逃逸到 return 后
                if len(self._candidates) > self.MAX_SPEAKERS * 2:
                    evict = sorted(self._candidates.keys())[:len(self._candidates) - self.MAX_SPEAKERS]
                    for k in evict:
                        del self._candidates[k]

                if len(self._candidates.get(best_ckey, [])) >= min_frames_to_reg:
                    # 短片段不注册为新说话人（embedding 不稳定）
                    if duration_s < self.MIN_DUR_REGISTER:
                        return None
                    c_embs = self._candidates.pop(best_ckey)
                    centroid = np.mean(c_embs, axis=0)
                    centroid /= (np.linalg.norm(centroid) + 1e-9)
                    self._centroids.append(centroid)
                    self._counts.append(len(c_embs))
                    new_id = len(self._centroids) - 1
                    print(f"[说话人] 检测到新说话人 → 发言人{new_id + 1}（累积{len(c_embs)}帧后注册）", flush=True)
                    return new_id if warmup_ready else None
                else:
                    # 积累未完成：返回 None
                    return None

            else:
                # ── 已达说话人上限（MAX_SPEAKERS） ────────────
                # 修复：仅在相似度真正达到阈值时才匹配，低于阈值返回 None
                # 原代码会把任意声音强制归入最相似的发言人，导致识别错误
                if best_sim >= match_threshold:
                    return best_idx if warmup_ready else None
                else:
                    return None

_speaker_tracker = SpeakerTracker()

# ─── 默认设置 ─────────────────────────────────────────────────
DEFAULT_SETTINGS = {
    "whisper_repo":     "mlx-community/whisper-large-v3-turbo",
    "sensevoice_repo":  "mlx-community/SenseVoiceSmall",
    "asr_backend":      "whisper",    # "whisper" | "sensevoice"
    "llm_repo":         "mlx-community/Qwen3-14B-4bit",
    "translate_to":   ["中文", "英文", "日文"],
    "translate_map":  {"中文":"Chinese","英文":"English","日文":"Japanese","韩文":"Korean",
                       "法文":"French","德文":"German","西班牙文":"Spanish"},
    "silence_s":      0.8,
    "vad_threshold":  0.40,
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
    "spk_threshold":  0.68,  # 说话人声纹匹配阈值（余弦相似度）：越高越严格
    "spk_min_frames": 2,     # 注册新说话人前需积累的最少句数
    "spk_warmup_s":   0.0,   # 启动阶段预热秒数（0=关闭预热，由 spk_min_frames 单独控制）
}

SETTINGS_FILE = Path.home() / ".asrlive_settings.json"

def load_settings() -> dict:
    s = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            saved = json.loads(SETTINGS_FILE.read_text())
            s.update(saved)
            if saved.get("silence_s") == 0.6 and saved.get("vad_threshold") == 0.45:
                s["silence_s"] = DEFAULT_SETTINGS["silence_s"]
                s["vad_threshold"] = DEFAULT_SETTINGS["vad_threshold"]
        except Exception:
            pass
    return s

def save_settings(s: dict):
    SETTINGS_FILE.write_text(json.dumps(s, ensure_ascii=False, indent=2))

# ─── 分段录音配置 ─────────────────────────────────────────────
SEGMENT_SECONDS = 300          # 每 5 分钟自动切一段
SEGMENT_FRAMES  = SEGMENT_SECONDS * 16000   # 对应帧数（float32）
HISTORY_MEM_MAX = 200          # 内存中最多保留最近 N 条 history
MIN_ASR_SECONDS = 0.65         # 太短的实时片段会显著拉低 Whisper 语言判断和准确率
PRE_ROLL_SECONDS = 0.4         # VAD start 前补一点音频，避免吞掉句首
MAX_CONTEXT_PROMPT_CHARS = 400 # 场景提示词过长会拖慢 Whisper/LLM 并污染输出

# ─── 全局状态 ──────────────────────────────────────────────────
class State:
    def __init__(self):
        self.recording   = False
        self.settings    = load_settings()
        self.history: list[dict] = []        # 内存仅保留最近 HISTORY_MEM_MAX 条
        self.ws_clients: list[WebSocket] = []
        self.asr_queue:  queue.Queue = queue.Queue(maxsize=20)  # 修复内存泄漏：限制队列深度，防止音频数组无限积压
        self.spk_queue:  queue.Queue = queue.Queue(maxsize=20)  # 说话人识别异步执行，避免阻塞 ASR
        self.context:    list[str] = []
        self._stream:    Optional[sd.InputStream] = None
        self.worker:     Optional[ModelWorker] = None
        # ── 分段录音 ──────────────────────────────────────────
        self._rec_chunks: list = []          # 当前段的 chunk 缓冲
        self._rec_frames: int  = 0           # 当前段已累积帧数
        self._rec_segments: list[str] = []   # 已落盘的分段 MP3 路径
        self._rec_session_dir: Optional[str] = None  # 临时目录
        self._rec_mp3_final: Optional[str] = None    # 最终合并 MP3 路径
        self._rec_final_path:  Optional[str] = None  # 最终合并路径
        self._seg_lock = threading.Lock()    # 保护分段写盘
        self._save_thread = None             # 最终合并线程引用
        self._flush_threads: list = []       # 中间分段 daemon 线程引用（用于 stop 时等待）
        self._last_saved_mp3: Optional[str] = None  # 最后保存的 MP3 路径（仅供记录）
        # ── history 持久化 ────────────────────────────────────
        self._history_file: Optional[str] = None   # JSONL 落盘文件
        self._history_lock = threading.Lock()
        # ── 其他 ─────────────────────────────────────────────
        self._downloading: bool = False
        self._worker_lock = threading.Lock()
        self._paused:      bool = False
        self._task_id    = 0
        self._task_id_lock = threading.Lock()   # 保护 _task_id 跨线程自增
        self._pending:   dict = {}  # task_id → asr 结果暂存
        self._spk_lock = threading.Lock()
        self._spk_results: dict = {}  # asr task_id → speaker_id（speaker_dispatcher写）
        self._spk_entry_pending: dict = {}  # asr task_id → entry_id（字幕先于说话人结果生成时使用）

G = State()

# ─── WebSocket 广播 ───────────────────────────────────────────
_main_loop: Optional[asyncio.AbstractEventLoop] = None
_SPK_PENDING = object()
_SPK_NO_RESULT = object()

def broadcast_sync(msg: dict):
    global _main_loop
    try:
        if _main_loop and _main_loop.is_running():
            asyncio.run_coroutine_threadsafe(_broadcast(msg), _main_loop)
    except Exception:
        pass

_broadcast_count = 0
async def _broadcast(msg: dict):
    global _broadcast_count
    dead = []
    for ws in list(G.ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in G.ws_clients:
            G.ws_clients.remove(ws)
    # 每 500 次广播做一次全量存活检查，清理可能漏过的死连接
    _broadcast_count += 1
    if _broadcast_count % 500 == 0:
        G.ws_clients[:] = [c for c in G.ws_clients
                           if not getattr(c, "client_state", None) or c.client_state.value <= 1]

# ─── 结果接收线程（轮询子进程 result_q）────────────────────────
def result_receiver():
    """持续从 ModelWorker 子进程读取结果并广播。
    修复竞态：先把 G.worker 快照到局部变量，避免检查后、get() 前
    worker 被替换为 None 或其他对象导致永久阻塞或 AttributeError。
    """
    while True:
        worker = G.worker          # 原子读取快照
        if worker is None:
            time.sleep(0.05)
            continue
        try:
            msg = worker.result_q.get(timeout=1.0)  # 超时轮询，支持干净退出
        except Exception:
            # Queue.Empty（timeout 到期）或队列已关闭，继续外层循环
            continue
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
        # 定期清理过期 pending（每 30 次 asr_done 执行一次，几乎零开销）
        G._asr_done_count = getattr(G, "_asr_done_count", 0) + 1
        if G._asr_done_count % 30 == 0:
            _purge_stale_pending()
        with G._spk_lock:
            spk_result = G._spk_results.pop(tid, _SPK_PENDING)
        speaker_id = None if spk_result in (_SPK_PENDING, _SPK_NO_RESULT) else spk_result
        spk_pending = spk_result is _SPK_PENDING
        if not raw:
            with G._spk_lock:
                G._spk_entry_pending.pop(tid, None)
            return

        # ── 改进①: 立即生成草稿 entry，让用户看到 raw 文本，无需等 LLM ──
        audio_start_time = msg.get("audio_start_time")
        if audio_start_time:
            ts_struct = time.localtime(audio_start_time)
            subtitle_ts = time.strftime("%H:%M:%S", ts_struct)
            rec_start = getattr(G, "_rec_start_time", None)
            mp3_offset_s = round(audio_start_time - rec_start, 3) if rec_start else None
        else:
            subtitle_ts  = time.strftime("%H:%M:%S")
            mp3_offset_s = None

        draft_id = int(time.time() * 1000)
        draft_entry = {
            "id":           draft_id,
            "raw":          raw,
            "corrected":    raw,           # 草稿先用 raw 占位
            "language":     lang,
            "translations": {},
            "asr_ms":       asr_ms,
            "llm_ms":       0,
            "timestamp":    subtitle_ts,
            "mp3_offset_s": mp3_offset_s,
            "speaker_id":   speaker_id,
            "draft":        True,          # 标记为草稿，前端显示"矫正中…"
        }
        _history_append(draft_entry)
        # 说话人晚到的情况同步处理
        if speaker_id is None and spk_pending:
            with G._spk_lock:
                G._spk_entry_pending[tid] = draft_id
        broadcast_sync({"type": "entry_draft", **draft_entry})

        # ── 改进②: LLM 分两步——先只做矫正（快），再做翻译（慢）──
        ctx = list(G.context[-G.settings["ctx_sentences"]:])
        prompt_correct = build_correction_prompt(raw, lang, ctx)
        with G._task_id_lock:
            G._task_id += 1
            llm_tid = G._task_id
        G._pending[llm_tid] = {
            "raw":              raw,
            "lang":             lang,
            "asr_ms":           asr_ms,
            "audio_start_time": audio_start_time,
            "speaker_id":       speaker_id,
            "asr_task_id":      tid if spk_pending else None,
            "draft_id":         draft_id,       # 对应草稿 entry 的 id
            "kind":             "correct",      # 第一步：仅矫正
            "_created_at":      time.time(),
        }
        G.worker.send({
            "kind": "llm", "task_id": llm_tid,
            "prompt": prompt_correct, "raw": raw, "lang": lang, "asr_ms": asr_ms,
            "audio_start_time": audio_start_time,
        })
        # context 在 correct 完成后用 corrected 追加，不在这里用 raw 追加

    elif t == "llm_done":
        resp   = msg.get("resp", "")
        llm_ms = msg.get("llm_ms", 0)
        raw    = msg.get("raw", "")
        lang   = msg.get("lang", "")
        asr_ms = msg.get("asr_ms", 0)
        tid = msg.get("task_id")
        pending_meta = G._pending.pop(tid, None)

        # ── 解析 LLM JSON 输出（共用逻辑）──────────────────────
        # 先移除已闭合的 <think>...</think> 块（Qwen3 thinking 模式）
        resp_clean = re.sub(r"<think>.*?</think>", "", resp, flags=re.DOTALL)
        # 若 <think> 块被 max_tokens 截断未闭合，丢弃推理内容只保留前段 JSON
        if "<think>" in resp_clean:
            parts = resp_clean.split("</think>", 1)
            resp_clean = parts[1] if len(parts) > 1 else resp_clean.split("<think>", 1)[0]
        text = re.sub(r"```json\s*|```", "", resp_clean).strip()
        out = {}
        try:
            out = json.loads(text)
        except Exception:
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
        if not isinstance(trans_raw, dict):
            print(f"[LLM] translations 格式异常: {type(trans_raw)}", flush=True)
            trans_raw = {}

        # ── 防幻觉安全网（仅对 correct 步骤生效）─────────────────
        # 如果 LLM 输出字数比原文多超过 15%，视为扩写幻觉，回退到 raw
        if pending_meta and pending_meta.get("kind") == "correct" and raw:
            raw_len  = len(raw)
            corr_len = len(corrected) if corrected else 0
            if corr_len > raw_len * 1.15 + 3:
                print(
                    f"[LLM] 矫正幻觉检测：输出({corr_len}字) > 原文({raw_len}字)×1.15，回退到 raw",
                    flush=True
                )
                corrected = raw
        rev_map  = {v: k for k, v in G.settings.get("translate_map", {}).items()}
        trans_cn = {rev_map.get(k, k): v for k, v in trans_raw.items() if isinstance(v, str)}

        # ── retranslate 任务：只更新已有卡片，不新增 entry ──────
        if pending_meta and pending_meta.get("kind") == "retranslate":
            entry_id      = pending_meta["entry_id"]
            new_corrected = pending_meta["corrected"]
            with G._history_lock:
                for e in G.history:
                    if str(e.get("id")) == str(entry_id):
                        e["corrected"]    = new_corrected
                        e["language"]     = language
                        e["translations"] = trans_cn
                        break
            broadcast_sync({
                "type":         "entry_updated",
                "id":           entry_id,
                "corrected":    new_corrected,
                "language":     language,
                "translations": trans_cn,
                "llm_ms":       llm_ms,
            })
            return

        # ── 改进②: 第一步完成（仅矫正）→ 立即升级草稿，再发翻译任务 ──
        if pending_meta and pending_meta.get("kind") == "correct":
            draft_id    = pending_meta["draft_id"]
            speaker_id  = pending_meta.get("speaker_id")
            asr_task_id = pending_meta.get("asr_task_id")

            # 更新 history 中的草稿记录
            with G._history_lock:
                for e in G.history:
                    if str(e.get("id")) == str(draft_id):
                        e["corrected"] = corrected
                        e["language"]  = language
                        e["llm_ms"]    = llm_ms
                        e["draft"]     = True   # 仍是草稿，翻译还没来
                        break

            # 说话人晚到处理
            if speaker_id is None and asr_task_id is not None:
                with G._spk_lock:
                    late_speaker = G._spk_results.pop(asr_task_id, _SPK_PENDING)
                    if late_speaker is _SPK_PENDING:
                        G._spk_entry_pending[asr_task_id] = draft_id
                if late_speaker not in (_SPK_PENDING, _SPK_NO_RESULT):
                    speaker_id = late_speaker
                    with G._history_lock:
                        for e in G.history:
                            if str(e.get("id")) == str(draft_id):
                                e["speaker_id"] = speaker_id
                                break

            # context 用 corrected 追加，让后续纠错更准确
            G.context.append(corrected or raw)
            if len(G.context) > 12:
                G.context = G.context[-12:]

            # 推送"矫正完成，翻译中"的中间态给前端
            broadcast_sync({
                "type":        "entry_corrected",
                "id":          draft_id,
                "corrected":   corrected,
                "language":    language,
                "speaker_id":  speaker_id,
                "asr_ms":      asr_ms,
                "llm_ms":      llm_ms,
            })

            # 如果没有翻译目标，直接标记完成
            tmap     = G.settings.get("translate_map", {})
            langs_cn = G.settings.get("translate_to", [])
            orig_en  = ASR_LANG_EN.get(language.lower(), "")
            langs_en = [tmap.get(l, l) for l in langs_cn
                        if tmap.get(l, l).lower() != orig_en.lower()]
            if not langs_en:
                with G._history_lock:
                    for e in G.history:
                        if str(e.get("id")) == str(draft_id):
                            e["draft"] = False
                            break
                broadcast_sync({"type": "entry_finalized", "id": draft_id, "translations": {}})
                return

            # 发翻译任务（第二步）
            prompt_trans = build_translation_prompt(corrected, language)
            with G._task_id_lock:
                G._task_id += 1
                trans_tid = G._task_id
            G._pending[trans_tid] = {
                "raw":          raw,
                "lang":         language,
                "asr_ms":       asr_ms,
                "draft_id":     draft_id,
                "speaker_id":   speaker_id,
                "kind":         "translate",   # 第二步：仅翻译
                "_created_at":  time.time(),
            }
            G.worker.send({
                "kind": "llm", "task_id": trans_tid,
                "prompt": prompt_trans, "raw": corrected, "lang": language,
                "asr_ms": asr_ms,
            })
            return

        # ── 第二步完成（翻译）→ 最终化 entry ─────────────────────
        if pending_meta and pending_meta.get("kind") == "translate":
            draft_id   = pending_meta["draft_id"]
            speaker_id = pending_meta.get("speaker_id")
            if not trans_cn and out:
                print(f"[LLM] 无翻译，out={json.dumps(out, ensure_ascii=False)[:200]}", flush=True)
            with G._history_lock:
                for e in G.history:
                    if str(e.get("id")) == str(draft_id):
                        e["translations"] = trans_cn
                        e["llm_ms"]       = (e.get("llm_ms") or 0) + llm_ms
                        e["draft"]        = False
                        break
            broadcast_sync({
                "type":         "entry_finalized",
                "id":           draft_id,
                "translations": trans_cn,
                "llm_ms":       llm_ms,
            })
            return

        # ── 兜底：未知 kind，按原逻辑处理（向后兼容）────────────
        print(f"[LLM] 未知 pending kind: {(pending_meta or {}).get('kind')}", flush=True)

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

def _purge_stale_pending(max_age_s: float = 120.0):
    """清理超过 max_age_s 秒未被消费的 _pending 条目，防止孤儿任务无限积压。
    在 worker 重启时调用，确保旧任务 ID 不残留。"""
    now = time.time()
    stale = [k for k, v in list(G._pending.items())
             if now - float(v.get("_created_at", now)) > max_age_s]
    for k in stale:
        G._pending.pop(k, None)
    if stale:
        print(f"[内存] 清理 {len(stale)} 条过期 _pending 任务", flush=True)


def ensure_model_worker(restart: bool = False):
    """Start or restart the model subprocess from the current settings."""
    with G._worker_lock:
        if G.worker is not None and not restart:
            proc = getattr(G.worker, "_proc", None)
            if proc is None or proc.is_alive():
                return False
        old_worker = G.worker
        G.worker = None
        if old_worker:
            old_worker.stop()
        # 重启时清理所有孤儿 pending（旧 worker 的结果永远不会回来）
        G._pending.clear()
        G.worker = ModelWorker(
            resolve_model_ref(G.settings["whisper_repo"]),
            resolve_model_ref(G.settings["llm_repo"]),
            sensevoice_repo=resolve_model_ref(G.settings.get("sensevoice_repo", "")),
            asr_backend=G.settings.get("asr_backend", "whisper"),
        )
        return True

def build_retranslate_prompt(text: str) -> str:
    """为「重新翻译」构建 prompt：自动检测语言并翻译到所有目标语言。"""
    tmap     = G.settings.get("translate_map", {})
    langs_cn = G.settings.get("translate_to", [])
    langs_en = [tmap.get(l, l) for l in langs_cn]
    # 每种语言单独一行，避免 LLM 在 inline object 中截断翻译内容
    trans_lines = "".join(f'    "{l}": "<translation>",\n' for l in langs_en)
    if trans_lines:
        trans_lines = trans_lines.rstrip(",\n") + "\n"
    trans_block = (
        f',\n  "translations": {{\n{trans_lines}  }}'
        if langs_en else ""
    )
    return (
        "<|im_start|>system\n"
        "You are a multilingual text processor. "
        "Detect the language of the given text, then translate it into all requested target languages. "
        "Reply ONLY with valid JSON. No explanation, no markdown.\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Text: \"{text}\"\n\n"
        "Fill in every field and return the JSON:\n"
        "{\n"
        "  \"corrected\": \"<copy the input text exactly as-is>\",\n"
        f"  \"language\": \"<Chinese|English|Japanese|Korean|French|German|Spanish>\"{trans_block}\n"
        "}\n"
        "/no_think<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n</think>\n"
    )

def build_correction_prompt(raw: str, lang: str, ctx: list) -> str:
    """第一步 LLM prompt：最小化 ASR 纠错，严格限制改写范围，防止幻觉。"""
    ctx_str   = "\n".join(f"[{i+1}] {s}" for i, s in enumerate(ctx))
    lang_enum = "|".join(sorted(v for v in set(ASR_LANG_EN.values()) if v))
    ctx_prompt = G.settings.get("context_prompt", "").strip()[:MAX_CONTEXT_PROMPT_CHARS]
    domain_line = (
        f"Terminology reference (use ONLY to fix homophones): {ctx_prompt}\n"
        if ctx_prompt else ""
    )
    raw_chars = len(raw)
    # 允许的最大字符数变化：10%，至少允许±3个字符（处理标点）
    max_delta = max(3, int(raw_chars * 0.10))
    min_len   = max(1, raw_chars - max_delta)
    max_len   = raw_chars + max_delta

    return (
        "<|im_start|>system\n"
        "You are a minimal ASR error corrector. Your ONLY job is to fix clear ASR recognition mistakes.\n"
        "STRICT RULES — violating any rule is WRONG:\n"
        "1. Fix ONLY: homophones (同音字), obvious misheard words, missing/wrong punctuation at sentence end.\n"
        "2. DO NOT rewrite, rephrase, expand, summarize, or improve the text in any way.\n"
        "3. DO NOT add content that was not in the original speech.\n"
        "4. DO NOT remove words unless they are clear ASR artifacts (e.g., repeated stutters like '的的的').\n"
        f"5. Output length must stay within {min_len}–{max_len} characters (original: {raw_chars} chars).\n"
        "6. If you are unsure whether something is an ASR error, leave it unchanged.\n"
        "7. Keep the original language. Reply ONLY with valid JSON.\n"
        f"{domain_line}"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Recent context:\n{ctx_str or '(none)'}\n\n"
        f"ASR output to correct ({lang}): \"{raw}\"\n\n"
        "Return ONLY:\n"
        "{\n"
        "  \"corrected\": \"<minimally corrected text>\",\n"
        f"  \"language\": \"<{lang_enum}>\"\n"
        "}\n"
        "/no_think<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n</think>\n"
    )


def build_translation_prompt(corrected: str, lang: str) -> str:
    """第二步 LLM prompt：仅做翻译，输入已是矫正后文本。"""
    tmap     = G.settings.get("translate_map", {})
    langs_cn = G.settings.get("translate_to", [])
    orig_en  = ASR_LANG_EN.get(lang.lower(), "")
    langs_en = [tmap.get(l, l) for l in langs_cn
                if tmap.get(l, l).lower() != orig_en.lower()]
    if not langs_en:
        return ""   # 无翻译目标，调用方不应发此 prompt

    trans_lines = "".join(f'    "{l}": "<translation>",\n' for l in langs_en)
    if trans_lines:
        trans_lines = trans_lines.rstrip(",\n") + "\n"

    return (
        "<|im_start|>system\n"
        "You are a multilingual translator. "
        "Translate the given text into all requested languages. "
        "Reply ONLY with valid JSON. No explanation, no markdown.\n"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Source ({lang}): \"{corrected}\"\n\n"
        "Return ONLY this JSON:\n"
        "{\n"
        f"  \"translations\": {{\n{trans_lines}  }}\n"
        "}\n"
        "/no_think<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n</think>\n"
    )


def build_llm_prompt(raw: str, lang: str, ctx: list) -> str:
    """向后兼容入口（retranslate 仍用此函数，内部合并矫正+翻译）。"""
    tmap      = G.settings.get("translate_map", {})
    langs_cn  = G.settings.get("translate_to", [])
    orig_en   = ASR_LANG_EN.get(lang.lower(), "")
    langs_en  = [tmap.get(l, l) for l in langs_cn
                 if tmap.get(l, l).lower() != orig_en.lower()]
    ctx_str   = "\n".join(f"[{i+1}] {s}" for i, s in enumerate(ctx))
    trans_lines = "".join(f'    "{l}": "<translation>",\n' for l in langs_en)
    if trans_lines:
        trans_lines = trans_lines.rstrip(",\n") + "\n"
    trans_block = (
        f',\n  "translations": {{\n{trans_lines}  }}'
        if langs_en else ""
    )
    ctx_prompt = G.settings.get("context_prompt", "").strip()[:MAX_CONTEXT_PROMPT_CHARS]
    domain_instruction = (
        f"Domain context and terminology reference:\n{ctx_prompt}\n"
        "Use the above to improve correction accuracy for domain-specific terms.\n"
        if ctx_prompt else ""
    )
    lang_enum = "|".join(sorted(v for v in set(ASR_LANG_EN.values()) if v))
    return (
        "<|im_start|>system\n"
        "You are a multilingual ASR post-processor. "
        "Fix homophones, punctuation, and recognition errors using context. "
        "Keep the original language unchanged. Reply ONLY with valid JSON. No explanation, no markdown.\n"
        f"{domain_instruction}"
        "<|im_end|>\n"
        "<|im_start|>user\n"
        f"Context (recent sentences):\n{ctx_str or '(none)'}\n\n"
        f"Raw ASR ({lang}): \"{raw}\"\n\n"
        "Fill in every field and return the JSON:\n"
        "{\n"
        "  \"corrected\": \"<corrected text, same language>\",\n"
        f"  \"language\": \"<{lang_enum}>\"{trans_block}\n"
        "}\n"
        "/no_think<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n</think>\n"
    )
# ─── ASR 音频队列处理线程 ─────────────────────────────────────
def _queue_drop_oldest(q: queue.Queue, item):
    try:
        q.put_nowait(item)
    except queue.Full:
        try:
            q.get_nowait()
        except queue.Empty:
            pass
        try:
            q.put_nowait(item)
        except queue.Full:
            pass


def _update_entry_speaker(entry_id, speaker_id):
    with G._history_lock:
        for e in G.history:
            if str(e.get("id")) == str(entry_id):
                e["speaker_id"] = speaker_id
                break
    broadcast_sync({
        "type": "speaker_id_updated",
        "entry_id": entry_id,
        "speaker_id": speaker_id,
    })


def speaker_dispatcher():
    """异步执行说话人识别，避免 pyannote 拖慢 ASR 派发。"""
    _last_speaker_id = None   # 最近一次成功识别的发言人，用于短句回落
    while True:
        item = G.spk_queue.get()
        if item is None:
            break
        task_id, audio = item
        speaker_id = _speaker_tracker.identify(
            audio,
            threshold=float(G.settings.get("spk_threshold", 0.68)),
            min_frames=int(G.settings.get("spk_min_frames", 2)),
            warmup_s=float(G.settings.get("spk_warmup_s", 0.0))
        )
        # 短句/低能量返回 None：回落到最近一次已知发言人，避免短句留空
        if speaker_id is None:
            if _last_speaker_id is not None:
                speaker_id = _last_speaker_id
                print(f"[说话人] 短句/低能量，回落到上一发言人: 发言人{speaker_id + 1}", flush=True)
            else:
                with G._spk_lock:
                    entry_id = G._spk_entry_pending.pop(task_id, None)
                    if entry_id is None:
                        G._spk_results[task_id] = _SPK_NO_RESULT
                    if len(G._spk_results) > 200:
                        for k in list(G._spk_results.keys())[:100]:
                            del G._spk_results[k]
                continue
        else:
            _last_speaker_id = speaker_id
        with G._spk_lock:
            entry_id = G._spk_entry_pending.pop(task_id, None)
            if entry_id is None:
                G._spk_results[task_id] = speaker_id
                if len(G._spk_results) > 200:
                    for k in list(G._spk_results.keys())[:100]:
                        del G._spk_results[k]
        if entry_id is not None:
            _update_entry_speaker(entry_id, speaker_id)


def asr_dispatcher():
    """从本地 asr_queue 取音频，立即发给 ASR 子进程。"""
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

        _queue_drop_oldest(G.spk_queue, (_id, audio.copy()))

        G.worker.send({
            "kind":             "asr",
            "task_id":          _id,
            "audio_bytes":      audio.tobytes(),
            "audio_start_time": audio_start_time,
            "initial_prompt":   (G.settings.get("context_prompt") or "")[:MAX_CONTEXT_PROMPT_CHARS] or None,
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
        pcm_bytes = pcm.tobytes()
        del audio, pcm   # 释放 float32/int16 数组，降低峰值内存
        proc = subprocess.run(cmd, input=pcm_bytes,
                              capture_output=True, timeout=120)
        del pcm_bytes
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
        G._rec_mp3_final  = final_path   # 记录最终 MP3 路径，供会话保存使用
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

# ─── 改进③: 公共音频回调工厂 ────────────────────────────────
# 原 start_audio / _resume_audio 各自定义了一份几乎完全相同的 cb() + flush_vad()，
# 现提取为工厂函数，两处共用，同时修复 start_audio 中缺少 _paused 检查的 bug。
#
# 改进④: 中日文短句合并逻辑
# 中日文停顿短，VAD 容易把一句话切成两段。
# 当识别语言为中文/日文，且 buf 内容估算 token 数 < MIN_CJK_CHARS 时，
# 不立即 flush，等待下一段合并（最多等 MAX_MERGE_WAIT_S 秒）。

MIN_CJK_CHARS    = 8    # 少于此字符数的中日文片段尝试与下一段合并
MAX_MERGE_WAIT_S = 3.0  # 最多等待此时长后强制 flush（防止无限挂起）

def _make_audio_callback(vad, s: dict, sentence_start_frames: list):
    """
    返回 (flush_vad, cb) 两个函数，共享 buf/pre_roll/speaking/sil_cnt 状态。
    sentence_start_frames: 长度为 1 的列表，调用方持有引用以读取起始帧。
    """
    CHUNK  = 512
    SIL_TH = max(1, round(s["silence_s"] * 16000 / CHUNK))
    MAX_FR = round(s["max_sentence_s"] * 16000 / CHUNK)
    PRE_FR = max(1, round(PRE_ROLL_SECONDS * 16000 / CHUNK))

    buf: list[np.ndarray] = []
    pre_roll  = collections.deque(maxlen=PRE_FR)
    sil_cnt   = [0]
    speaking  = [False]
    _level_tick = [0]

    # ── 改进④: 中日文短句合并缓冲 ──────────────────────────
    _pending_merge: list[np.ndarray] = []    # 等待合并的前一段音频
    _pending_merge_ts = [0.0]                # 前一段的 audio_start_time
    _pending_merge_at = [0.0]                # 进入缓冲时的 wall time（超时用）

    def _try_dispatch(audio: np.ndarray, audio_start_time: float):
        """判断是否需要与前段合并，再决定是否送 ASR 队列。"""
        nonlocal _pending_merge
        # 估算字符数：中日文约 2 token/字，用时长粗估
        # 实际帧数 / 16000 = 秒数，中日文约 5字/秒
        dur_s      = len(audio) / 16000.0
        asr_lang   = s.get("asr_language") or ""
        is_cjk     = asr_lang in ("zh", "chinese", "ja", "japanese", "")
        est_chars  = dur_s * 5   # 粗估字数

        if is_cjk and est_chars < MIN_CJK_CHARS:
            # 本段太短：先存入合并缓冲
            if _pending_merge:
                # 已有待合并段，拼接后一起发
                merged = np.concatenate(_pending_merge + [audio])
                _pending_merge.clear()
                ts = _pending_merge_ts[0]
                print(f"[VAD] 中日文短句合并 ({dur_s:.2f}s) → 合计 {len(merged)/16000:.2f}s", flush=True)
                _queue_drop_oldest(G.asr_queue, (merged, ts))
            else:
                _pending_merge.extend([audio])
                _pending_merge_ts[0]  = audio_start_time
                _pending_merge_at[0]  = time.monotonic()
                print(f"[VAD] 中日文短句缓冲 ({dur_s:.2f}s)，等待下段合并", flush=True)
        else:
            # 本段够长：如果有待合并段，先把它接上
            if _pending_merge:
                merged = np.concatenate(_pending_merge + [audio])
                _pending_merge.clear()
                ts = _pending_merge_ts[0]
                print(f"[VAD] 中日文合并完成 → {len(merged)/16000:.2f}s", flush=True)
                _queue_drop_oldest(G.asr_queue, (merged, ts))
            else:
                _queue_drop_oldest(G.asr_queue, (audio, audio_start_time))

    def _flush_pending_merge():
        """超时后强制发送待合并缓冲（防止最后一段挂起）。"""
        if _pending_merge:
            audio = np.concatenate(_pending_merge)
            _pending_merge.clear()
            if len(audio) / 16000 >= MIN_ASR_SECONDS:
                _queue_drop_oldest(G.asr_queue, (audio, _pending_merge_ts[0]))

    def flush_vad():
        if not buf:
            return
        audio = np.concatenate(buf)
        if len(audio) / 16000 >= MIN_ASR_SECONDS:
            audio_start_time = G._rec_start_time + sentence_start_frames[0] / 16000.0
            _try_dispatch(audio, audio_start_time)
        # 超时检查：如果合并缓冲滞留超过 MAX_MERGE_WAIT_S，强制发出
        if _pending_merge and (time.monotonic() - _pending_merge_at[0]) > MAX_MERGE_WAIT_S:
            _flush_pending_merge()
        buf.clear()
        sil_cnt[0]  = 0
        speaking[0] = False
        vad.reset_states()
        sentence_start_frames[0] = G._rec_frames_total

    def cb(indata, frames, t_, status_):
        # ── 改进③: 统一在此检查 _paused，原 start_audio 版本缺此检查 ──
        if not G.recording or G._paused:
            return
        chunk = indata[:, 0].copy().astype(np.float32)

        # 软件增益
        gain = float(G.settings.get("mic_gain", 1.5))
        if gain != 1.0:
            chunk = np.clip(chunk * gain, -1.0, 1.0)
        G._rec_frames_total += len(chunk)

        # 实时音量广播（节流：每 2 帧一次，≈15 fps）
        _level_tick[0] += 1
        if _level_tick[0] >= 2:
            _level_tick[0] = 0
            rms = float(np.sqrt(np.mean(chunk ** 2)))
            broadcast_sync({"type": "audio_level", "rms": rms})

        # 分段录音累积
        if G.settings.get("save_recording", True):
            G._rec_chunks.append(chunk)
            G._rec_frames += len(chunk)
            if G._rec_frames >= SEGMENT_FRAMES:
                G._flush_threads = [t for t in G._flush_threads if t.is_alive()]
                _ft = threading.Thread(target=_flush_segment, daemon=True)
                G._flush_threads.append(_ft)
                _ft.start()

        # VAD
        ev = vad(chunk, return_seconds=False)
        if ev:
            if "start" in ev:
                if not speaking[0] and not buf and pre_roll:
                    sentence_start_frames[0] = max(
                        0,
                        G._rec_frames_total - len(chunk) - sum(len(c) for c in pre_roll)
                    )
                    buf.extend(c.copy() for c in pre_roll)
                speaking[0] = True
                sil_cnt[0]  = 0
            if "end" in ev:
                flush_vad()
                return
        if speaking[0]:
            buf.append(chunk)
            sil_cnt[0] = 0
            if len(buf) >= MAX_FR:
                flush_vad()
        elif buf:
            sil_cnt[0] += 1
            buf.append(chunk)
            if sil_cnt[0] >= SIL_TH:
                flush_vad()
        else:
            pre_roll.append(chunk)

    return flush_vad, cb, _flush_pending_merge


def _validate_device(device_id) -> object:
    """检查设备 ID 是否可用，不可用时返回 None（系统默认）。"""
    if device_id is None:
        return None
    try:
        sd.query_devices(device_id)
        return device_id
    except Exception:
        print(
            f"[录音] 设备 {device_id} 不可用（已拔出或 ID 变更），回退到系统默认麦克风",
            flush=True,
        )
        broadcast_sync({
            "type": "error",
            "msg":  f"麦克风设备 {device_id} 不可用，已自动切换到系统默认设备。如需指定设备，请在设置中重新选择。",
        })
        return None


def _start_stream_with_retry(cb, s: dict) -> bool:
    """创建 InputStream 并重试最多 3 次，成功返回 True，失败返回 False。"""
    CHUNK = 512
    device = _validate_device(s.get("input_device"))
    G._stream = sd.InputStream(
        samplerate=16000, blocksize=CHUNK,
        channels=1, dtype="float32",
        device=device, callback=cb,
    )
    last_err = None
    for attempt in range(3):
        try:
            G._stream.start()
            last_err = None
            break
        except sd.PortAudioError as e:
            last_err = e
            wait = 0.8 * (attempt + 1)
            print(f"[录音] 启动音频流失败（第{attempt+1}次），{wait:.1f}s 后重试… {e}", flush=True)
            try:
                G._stream.stop()
                G._stream.close()
            except Exception:
                pass
            time.sleep(wait)
            G._stream = sd.InputStream(
                samplerate=16000, blocksize=CHUNK,
                channels=1, dtype="float32",
                device=device, callback=cb,
            )
    if last_err:
        G._stream = None
        return False
    return True


def start_audio():
    from silero_vad import load_silero_vad, VADIterator
    s     = G.settings
    vad_m = load_silero_vad(onnx=True)
    vad   = VADIterator(vad_m, threshold=s["vad_threshold"],
                        sampling_rate=16000,
                        min_silence_duration_ms=int(s["silence_s"] * 1000))

    # 初始化分段录音
    ts = time.strftime("%Y%m%d_%H%M%S")
    downloads = Path.home() / "Downloads"
    downloads.mkdir(exist_ok=True)
    G._rec_final_path   = str(downloads / f"ASRLive_{ts}.mp3")
    G._rec_session_dir  = str(downloads / f".asrlive_tmp_{ts}")
    os.makedirs(G._rec_session_dir, exist_ok=True)
    G._rec_chunks       = []
    G._rec_frames       = 0
    G._rec_segments     = []
    G._rec_start_time   = time.time()
    G._rec_frames_total = 0
    _history_init()
    print(f"[录音] 开始分段录制，最终输出 → {G._rec_final_path}", flush=True)

    sentence_start_frames = [0]
    _, cb, _ = _make_audio_callback(vad, s, sentence_start_frames)

    ok = _start_stream_with_retry(cb, s)
    if not ok:
        G.recording = False
        if G._rec_session_dir and os.path.exists(G._rec_session_dir):
            import shutil
            shutil.rmtree(G._rec_session_dir, ignore_errors=True)
        G._rec_session_dir = None
        G._rec_final_path  = None
        G._rec_segments    = []
        errmsg = (
            f"无法启动麦克风（PortAudio 错误）\n"
            f"请检查：① 麦克风权限 ② 蓝牙设备是否就绪 ③ 在系统设置中切换一次输入设备后重试"
        )
        print(f"[录音] 放弃：{errmsg}", flush=True)
        broadcast_sync({"type": "recording", "value": False})
        broadcast_sync({"type": "error",     "msg":   errmsg})

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

    # 等待所有中间分段的 daemon 写盘线程完成，再做最终 flush 和合并
    pending = [t for t in G._flush_threads if t.is_alive()]
    if pending:
        print(f"[录音] 等待 {len(pending)} 个分段写盘线程完成…", flush=True)
        for t in pending:
            t.join(timeout=8)
    G._flush_threads.clear()

    # 把最后一段（不足 5 分钟的尾巴）也写盘
    if G._rec_chunks:
        _flush_segment()
    # 修复内存泄漏：写盘后确保引用被释放
    G._rec_chunks = []
    G._rec_frames = 0
    # 清理本次录音的 history JSONL 临时文件（已全量载入内存或写入 .asr，不需要保留）
    if G._history_file and os.path.exists(G._history_file):
        try:
            os.remove(G._history_file)
        except Exception:
            pass
        G._history_file = None

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
    else:
        # 没有可合并的分段：可能是录音从未成功启动（如 PortAudio -9986）
        # 或 save_recording=False 分支已提前 return
        has_chunks = bool(G._rec_chunks)
        has_segs   = bool(G._rec_segments)
        print(f"[录音] 停止时无分段可合并 "
              f"(segments={has_segs}, chunks={has_chunks}, "
              f"final_path={G._rec_final_path!r})", flush=True)
        # 如果还有未落盘的 chunk（极少情况），强制同步写盘再合并
        if G._rec_chunks and G._rec_final_path and G._rec_session_dir:
            print("[录音] 发现未落盘 chunks，尝试紧急写盘…", flush=True)
            _flush_segment()
            if G._rec_segments:
                segments   = list(G._rec_segments)
                final_path = G._rec_final_path
                G._rec_segments  = []
                G._rec_final_path = None
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
    s     = G.settings
    vad_m = load_silero_vad(onnx=True)
    vad   = VADIterator(vad_m, threshold=s["vad_threshold"],
                        sampling_rate=16000,
                        min_silence_duration_ms=int(s["silence_s"] * 1000))

    # 继续使用现有 session，不重置 _rec_segments / _rec_final_path
    G._rec_chunks = []
    G._rec_frames = 0
    print(f"[录音] 继续录制，已有 {len(G._rec_segments)} 个分段，session → {G._rec_session_dir}", flush=True)

    sentence_start_frames = [G._rec_frames_total]
    _, cb, _ = _make_audio_callback(vad, s, sentence_start_frames)

    # 始终关闭旧流再重新创建，避免 CoreAudio/蓝牙切换后静默失败
    if G._stream:
        try:
            G._stream.stop()
        except Exception:
            pass
        try:
            G._stream.close()
        except Exception:
            pass
        G._stream = None

    ok = _start_stream_with_retry(cb, s)
    if not ok:
        G.recording = False
        broadcast_sync({"type": "recording", "value": False})
        broadcast_sync({"type": "error", "msg": "继续录音失败：无法重启麦克风流，请检查音频设备后重试。"})

# ─── FastAPI lifespan ─────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    global _main_loop
    _main_loop = asyncio.get_running_loop()

    # 结果接收线程。模型子进程延迟到下载引导结束后再启动，避免下载阶段
    # 与 MLX/LLM multiprocessing 预热互相干扰。
    threading.Thread(target=result_receiver, daemon=True).start()
    # ASR 分发线程
    threading.Thread(target=asr_dispatcher, daemon=True).start()
    # 说话人识别线程，与 ASR 解耦
    threading.Thread(target=speaker_dispatcher, daemon=True).start()
    # 预热说话人识别模型（后台线程，不阻塞启动）
    if _speaker_tracker.packages_ok:
        def _preload_speaker():
            print("[说话人] 后台预加载模型…", flush=True)
            _speaker_tracker._ensure_model()
        threading.Thread(target=_preload_speaker, daemon=True).start()

    yield

    stop_audio()
    # 先立即停止 worker（不再需要推理）
    if G.worker:
        G.worker.stop()
        G.worker = None
    # 等待 MP3 保存完成（最多 10 秒，比原来的 30 秒更短）
    if G._save_thread and G._save_thread.is_alive():
        print("[退出] 等待录音保存完成…", flush=True)
        G._save_thread.join(timeout=10)
        if G._save_thread.is_alive():
            print("[退出] MP3 合并超时，强制退出", flush=True)

# ─── FastAPI 应用 ─────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(title="Lancer1911 ASR Live", lifespan=lifespan)

    static = Path(__file__).parent / "static"
    if static.exists():
        app.mount("/static", StaticFiles(directory=str(static)), name="static")

    @app.get("/ping")
    def ping(): return {"ok": True}

    @app.get("/api/author")
    def author_token():
        # SHA-256("Lancer1911")，不含明码；前端再做一次 SHA-256 与硬编码值比对
        return {"token": "d3dc834172883521ce721e5bd82a036d302277f81e30e6ee598a85e2918a187d"}

    @app.get("/api/models")
    def api_models(): return JSONResponse(scan_local_models())

    @app.post("/api/start_worker")
    async def api_start_worker():
        """Start the ASR/LLM subprocess after model downloads are resolved."""
        await asyncio.to_thread(ensure_model_worker)
        return JSONResponse({"ok": True})

    @app.get("/api/check_models")
    def api_check_models():
        """返回推荐模型的本地缓存状态，以及 SenseVoice 依赖（mlx-audio）是否安装。"""
        # 检测 mlx-audio 是否安装（SenseVoice 运行时依赖）
        try:
            import importlib
            importlib.import_module("mlx_audio.stt.utils")
            mlx_audio_ok = True
        except ImportError:
            mlx_audio_ok = False

        result = {}
        for repo_id, info in RECOMMENDED.items():
            entry = {**info, "cached": is_model_cached(repo_id)}
            if info.get("type") == "sensevoice":
                entry["mlx_audio_installed"] = mlx_audio_ok
            result[repo_id] = entry
        return JSONResponse(result)

    @app.post("/api/download/{repo_path:path}")
    async def api_download(repo_path: str):
        """Download one model and return only after it has completed.

        Progress is still broadcast over WebSocket, but the HTTP response is
        the authoritative signal used by the frontend download queue. This
        prevents the UI from getting stuck if the final websocket message is
        missed during reconnect/shutdown races.
        """
        if G._downloading:
            return JSONResponse({"ok": False, "msg": "已有下载任务进行中"})
        G._downloading = True
        import queue as Q
        q = Q.Queue()
        def relay():
            while True:
                msg = q.get()
                if msg is None:
                    break
                broadcast_sync(msg)
        t = threading.Thread(target=relay, daemon=True)
        t.start()
        try:
            success = await asyncio.to_thread(download_model, repo_path, q)
            q.put(None)
            t.join(timeout=3)
            return JSONResponse({"ok": True, "success": bool(success)})
        except Exception as e:
            q.put({"type": "dl_error", "repo": repo_path, "msg": str(e)})
            q.put(None)
            t.join(timeout=3)
            return JSONResponse({"ok": False, "success": False, "msg": str(e)})
        finally:
            G._downloading = False

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
            "type":             "init",
            "recording":        G.recording,
            "settings":         G.settings,
            "history":          G.history[-60:],
            "speaker_names":    _speaker_tracker.get_names(),
            "speaker_packages_ok": _speaker_tracker.packages_ok,
            "speaker_active":   _speaker_tracker._enabled,
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
            if G.worker is None:
                await asyncio.to_thread(ensure_model_worker)
            G.recording = True
            G._paused   = False
            _speaker_tracker.reset()   # 新录音开始，清空说话人状态
            with G._spk_lock:
                G._spk_results.clear()
                G._spk_entry_pending.clear()
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
            # 暂停：先停流，再把当前未写盘的 chunks flush 到分段文件
            if G._stream:
                try: G._stream.stop()
                except Exception: pass
            # 等待正在进行的分段写盘线程完成
            pending = [t for t in G._flush_threads if t.is_alive()]
            for t in pending:
                t.join(timeout=10)
            # 把暂停前缓冲的音频写盘，保留到 _rec_segments
            if G._rec_chunks and G._rec_session_dir:
                G._flush_threads = [t for t in G._flush_threads if t.is_alive()]
                ft = threading.Thread(target=_flush_segment, daemon=True)
                G._flush_threads.append(ft)
                ft.start()
                ft.join(timeout=15)   # 暂停时同步等待写盘完成
            G._rec_chunks = []
            G._rec_frames = 0
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
            await _broadcast({"type": "status", "text": "加载模型中，请稍候…", "ready": False})
            if G.recording:
                # 先将 recording 置 False，stop_audio 本身不修改此标志；
                # 新 start_audio 线程启动后再置回 True，防止双重写盘竞态。
                G.recording = False
                G._paused   = False
                stop_audio()
                G.recording = True
                threading.Thread(target=start_audio, daemon=True).start()
            # 如果 ASR 后端/模型仓库发生变化，需重启 worker
            # （录音未运行时也执行，因为 worker 携带模型配置）
            # 注：此处简单重建 worker；若正在录音，上面已重启音频，worker 也需同步
            await asyncio.to_thread(ensure_model_worker, True)
            await _broadcast({"type": "settings", "settings": G.settings})
        elif act == "clear":
            # 若正在录音/暂停，先完整走停止流程，再清空
            # 否则 _rec_session_dir 会被 rmtree 掉，stop_audio 无法写盘，
            # _save_thread 不启动，rec_saved 永不来，前端 recState 卡在 'merging'
            if G.recording or getattr(G, "_paused", False):
                G.recording = False
                G._paused   = False
                stop_audio()
                await _broadcast({"type": "recording", "value": False})
            _speaker_tracker.reset()   # 清空时重置说话人状态
            with G._spk_lock:
                G._spk_results.clear()
                G._spk_entry_pending.clear()
            with G._history_lock:
                G.history.clear()
            # 清空磁盘 history 文件
            if G._history_file and os.path.exists(G._history_file):
                try: os.remove(G._history_file)
                except Exception: pass
            G.context.clear()
            # 清空临时分段目录（录音已完成或已被 stop_audio 处理，临时文件可删）
            if G._rec_session_dir and os.path.isdir(G._rec_session_dir):
                try:
                    import shutil
                    shutil.rmtree(G._rec_session_dir, ignore_errors=True)
                except Exception: pass
                G._rec_session_dir = None
                G._rec_segments = []
            G._last_saved_mp3 = None
            G._rec_mp3_final  = None
            G._rec_chunks = []; G._rec_frames = 0
            await _broadcast({"type":"cleared"})
        elif act == "update_speaker_id":
            # 手工修正：用户在前端将某条 entry 的说话人改为其他
            entry_id   = msg.get("entry_id")
            speaker_id = msg.get("speaker_id")  # int or None
            if entry_id is not None:
                with G._history_lock:
                    for e in G.history:
                        if str(e.get("id")) == str(entry_id):
                            e["speaker_id"] = speaker_id
                            break
                await _broadcast({"type": "speaker_id_updated",
                                  "entry_id": entry_id,
                                  "speaker_id": speaker_id})

        elif act == "rename_speaker":
            sid  = msg.get("speaker_id")
            name = msg.get("name", "")
            if isinstance(sid, int):
                _speaker_tracker.set_name(sid, name)
                await _broadcast({"type": "speaker_renamed", "speaker_id": sid, "name": name})

        elif act == "export":
            txt = _export(msg.get("format","txt"), msg.get("lang_filter","all"))
            await ws.send_json({"type":"export","format":msg.get("format"),"content":txt})

        elif act == "save_file":
            filename = msg.get("filename", "export.txt")
            file_content = msg.get("content", "")
            _save_file_dialog(filename, file_content, ws)

        elif act == "save_session":
            # 如果 MP3 合并线程仍在运行（刚停止录音），等待其完成再取路径
            if G._save_thread and G._save_thread.is_alive():
                await ws.send_json({"type": "status_msg",
                                    "text": "正在等待录音保存完成…"})
                await asyncio.to_thread(G._save_thread.join, 30)
                if G._save_thread.is_alive():
                    await ws.send_json({"type": "status_msg",
                                        "text": "录音保存超时，MP3 路径可能为空"})
            # 构建完整 .asr 会话文件（JSON）
            entries = _history_load_all()
            session = {
                "version": "4.6a",
                "saved_at":     time.strftime("%Y-%m-%dT%H:%M:%S"),
                "mp3_filename": G._rec_mp3_final or None,  # 录音 MP3 路径（如已保存）
                "settings":     {k: v for k, v in G.settings.items()
                                 if k not in ("input_device",)},  # 不保存设备ID
                "speaker_names": _speaker_tracker.get_names(),
                "entries":      entries,
            }
            content = json.dumps(session, ensure_ascii=False, indent=2)
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"ASRLive_{ts}.asr"
            _save_file_dialog(filename, content, ws)

        elif act == "load_session":
            # 前端传来 .asr 文件内容（字符串），解析后广播给所有客户端
            raw_content = msg.get("content", "")
            try:
                session = json.loads(raw_content)
                entries = session.get("entries", [])
                mp3_filename = session.get("mp3_filename")
                # 清空当前内存 history
                with G._history_lock:
                    G.history.clear()
                # 把加载的 entries 写入内存（最多 HISTORY_MEM_MAX 条）
                with G._history_lock:
                    G.history = entries[-HISTORY_MEM_MAX:]
                # 广播：先 clear，再逐条 entry，最后通知 mp3 路径
                broadcast_sync({"type": "clear"})
                # 恢复说话人名称
                spk_names = session.get("speaker_names", {})
                if spk_names:
                    _speaker_tracker._names.clear()
                    for sid, name in spk_names.items():
                        _speaker_tracker.set_name(int(sid), name)
                    broadcast_sync({"type": "speaker_names_loaded", "names": {str(k):v for k,v in spk_names.items()}})
                for e in entries:
                    broadcast_sync({"type": "entry", **e})
                mp3_found = False
                if mp3_filename and os.path.exists(mp3_filename):
                    G._rec_mp3_final = mp3_filename
                    broadcast_sync({"type": "rec_saved", "path": mp3_filename})
                    mp3_found = True
                await ws.send_json({"type":     "session_loaded",
                                    "count":    len(entries),
                                    "mp3":      mp3_filename or "",
                                    "mp3_found": mp3_found})
            except Exception as ex:
                await ws.send_json({"type": "error", "msg": f"加载失败：{ex}"})

        elif act == "retranslate":
            # 前端传来修改后的原文，重新检测语言并翻译
            entry_id  = msg.get("id")
            new_text  = msg.get("text", "").strip()
            if not entry_id or not new_text:
                await ws.send_json({"type": "error", "msg": "retranslate: 缺少参数"})
            else:
                # 用 LLM 检测语言 + 翻译，复用 build_llm_prompt
                # 语言设为 "auto"，让 LLM 自行判断
                prompt = build_retranslate_prompt(new_text)
                with G._task_id_lock:
                    G._task_id += 1
                    tid = G._task_id
                G._pending[tid] = {"entry_id": entry_id, "corrected": new_text,
                                   "kind": "retranslate",
                                   "_created_at": time.time()}
                G.worker.send({
                    "kind": "llm", "task_id": tid,
                    "prompt": prompt, "raw": new_text, "lang": "auto",
                    "asr_ms": 0,
                })

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
                # 根据文件扩展名推断 file_types 过滤器
                ext = os.path.splitext(filename)[1].lower()
                _file_types_map = {
                    ".asr":  "ASR 会话文件 (*.asr)",
                    ".txt":  "文本文件 (*.txt)",
                    ".srt":  "SRT 字幕文件 (*.srt)",
                    ".json": "JSON 文件 (*.json)",
                    ".md":   "Markdown 文件 (*.md)",
                }
                ft = _file_types_map.get(ext, f"文件 (*{ext})")
                save_path = wins[0].create_file_dialog(
                    _wv.SAVE_DIALOG,
                    directory=str(Path.home() / "Downloads"),
                    save_filename=filename,
                    file_types=(ft, "所有文件 (*.*)"),
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
    spk_names = _speaker_tracker.get_names()  # {0: "张三", 1: "李四", ...}

    def spk_label(e):
        sid = e.get("speaker_id")
        if sid is None:
            return ""
        name = spk_names.get(int(sid)) or spk_names.get(str(sid))
        return name if name else f"发言人{int(sid)+1}"
    if fmt == "txt":
        out = []
        for e in h:
            if lang_filter == "all":
                spk = spk_label(e)
                spk_str = f" [{spk}]" if spk else ""
                out.append(f"[{e['timestamp']}]{spk_str} [{e['language']}]")
                out.append(e.get("corrected", ""))
                for lang, text in e.get("translations", {}).items():
                    out.append(f"  → {lang}：{text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                spk = spk_label(e)
                spk_str = f" [{spk}]" if spk else ""
                out.append(f"[{e['timestamp']}]{spk_str} {t}")
            out.append("")
        return "\n".join(out)

    if fmt == "srt":
        out = []
        idx = 1
        for e in h:
            ts = e["timestamp"]       # HH:MM:SS 壁钟时间
            if lang_filter == "all":
                spk = spk_label(e)
                prefix = f"[{spk}] " if spk else ""
                lines = [prefix + e.get("corrected","")]
                for lang, text in e.get("translations",{}).items():
                    lines.append(f"[{lang}] {text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                spk = spk_label(e)
                prefix = f"[{spk}] " if spk else ""
                lines = [prefix + t]

            # 修复 #4：用录音偏移量生成有意义的起止时间戳
            # mp3_offset_s 是句子在录音中的精确起始秒数（float）
            offset = e.get("mp3_offset_s")
            if offset is not None:
                def _fmt_srt(secs):
                    secs = max(0.0, float(secs))
                    h_  = int(secs // 3600)
                    m_  = int((secs % 3600) // 60)
                    s_  = int(secs % 60)
                    ms_ = int(round((secs - int(secs)) * 1000))
                    return f"{h_:02d}:{m_:02d}:{s_:02d},{ms_:03d}"
                # 用文本字符数估算时长（最短 1 s，最长 30 s）
                char_count = sum(len(l) for l in lines)
                dur = max(1.0, min(30.0, char_count * 0.07))
                srt_start = _fmt_srt(offset)
                srt_end   = _fmt_srt(offset + dur)
            else:
                # 兜底：用壁钟时间，起止各差 3 秒（总比相同好）
                srt_start = f"{ts},000"
                srt_end   = f"{ts},000"
                # 尝试在结束时间加 3 秒
                try:
                    import datetime as _dt
                    t_obj = _dt.datetime.strptime(ts, "%H:%M:%S") + _dt.timedelta(seconds=3)
                    srt_end = t_obj.strftime("%H:%M:%S") + ",000"
                except Exception:
                    pass

            out += [str(idx), f"{srt_start} --> {srt_end}"] + lines + [""]
            idx += 1
        return "\n".join(out)

    if fmt == "json":
        if lang_filter == "all":
            return json.dumps(h, ensure_ascii=False, indent=2)
        filtered = []
        for e in h:
            t = _get_text(e, lang_filter)
            if t:
                item = {
                    "timestamp":  e["timestamp"],
                    "language":   lang_filter,
                    "text":       t,
                }
                spk = spk_label(e)
                if spk:
                    item["speaker"] = spk
                filtered.append(item)
        return json.dumps(filtered, ensure_ascii=False, indent=2)

    if fmt == "md":
        out = ["# Lancer1911 ASR Live 字幕记录\n"]
        for e in h:
            if lang_filter == "all":
                spk = spk_label(e)
                spk_str = f" **{spk}**" if spk else ""
                out.append(f"**[{e['timestamp']}]**{spk_str} `{e['language']}`\n")
                out.append(f"> {e.get('corrected','')}\n")
                for lang, text in e.get("translations",{}).items():
                    out.append(f"- **{lang}**：{text}")
            else:
                t = _get_text(e, lang_filter)
                if not t:
                    continue
                spk = spk_label(e)
                spk_str = f" **{spk}**" if spk else ""
                out.append(f"**[{e['timestamp']}]**{spk_str} {t}\n")
            out.append("")
        return "\n".join(out)

    return ""
