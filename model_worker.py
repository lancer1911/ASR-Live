"""
model_worker.py — 独立子进程，完全脱离 asyncio/uvicorn 环境
专门负责 ASR（Whisper 或 SenseVoiceSmall）+ Qwen3 LLM，通过 multiprocessing.Queue 通信

支持 asr_backend:
  "whisper"     — mlx_whisper（默认，支持 initial_prompt / language 锁定）
  "sensevoice"  — mlx_audio.stt SenseVoiceSmall（更快，支持情绪/事件检测）
"""
import json, os, re, time, numpy as np
from multiprocessing import Process, Queue

# 强制离线模式：跳过 huggingface_hub 的网络版本检查
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

MAX_INITIAL_PROMPT_CHARS = 400

# SenseVoice 语言代码映射（asr_language → SenseVoice lid_dict key）
_SV_LANG_MAP = {
    "zh": "zh", "chinese": "zh",
    "en": "en", "english":  "en",
    "ja": "ja", "japanese": "ja",
    "ko": "ko", "korean":   "ko",
    "yue": "yue",
}

# SenseVoice 输出语言名称 → 内部统一代码
_SV_LANG_OUT = {
    "Chinese":  "zh", "chinese":  "zh",
    "English":  "en", "english":  "en",
    "Japanese": "ja", "japanese": "ja",
    "Korean":   "ko", "korean":   "ko",
    "Cantonese":"yue",
    "Unknown":  "",   "unknown":  "",
}


def _prepare_asr_audio(audio: np.ndarray) -> np.ndarray:
    """Lightweight normalization before Whisper without changing timing."""
    if audio.size == 0:
        return audio
    audio = audio.astype(np.float32, copy=True)
    audio -= float(np.mean(audio))
    peak = float(np.max(np.abs(audio)))
    if peak > 0.98:
        audio *= 0.98 / peak
    rms = float(np.sqrt(np.mean(audio ** 2)))
    if 1e-6 < rms < 0.035:
        target = min(0.08, rms * 2.5)
        audio *= target / rms
        np.clip(audio, -0.98, 0.98, out=audio)
    return audio


def _hallucination_filter(raw: str) -> str:
    """检测重复词幻觉（如 'nope nope nope...'），返回空字符串表示过滤。"""
    if not raw:
        return raw
    words = raw.split()
    if len(words) >= 6:
        top = max(set(words[:20]), key=words[:20].count)
        ratio = words[:20].count(top) / min(len(words), 20)
        if ratio > 0.5:
            print(f"[ASR] 幻觉过滤: {raw[:60]}…", flush=True)
            return ""
    return raw


# ── Whisper 后端 ──────────────────────────────────────────────

def _run_whisper(audio: np.ndarray, task: dict, whisper_repo: str) -> dict:
    import mlx_whisper
    audio = _prepare_asr_audio(audio)
    t0 = time.perf_counter()

    initial_prompt = task.get("initial_prompt") or None
    if initial_prompt and len(initial_prompt) > MAX_INITIAL_PROMPT_CHARS:
        initial_prompt = initial_prompt[:MAX_INITIAL_PROMPT_CHARS]
    asr_language = task.get("asr_language") or None
    if asr_language:
        print(f"[ASR/Whisper] 语言锁定: {asr_language}", flush=True)

    res = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=whisper_repo,
        language=asr_language,
        word_timestamps=False,
        fp16=True,
        temperature=0.0,
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        compression_ratio_threshold=2.0,
        logprob_threshold=-1.0,
        initial_prompt=initial_prompt,
    )
    asr_ms = round((time.perf_counter() - t0) * 1000)
    raw = _hallucination_filter(res.get("text", "").strip())
    return {"raw": raw, "lang": res.get("language", ""), "asr_ms": asr_ms}


# ── SenseVoice 后端 ───────────────────────────────────────────

def _load_sensevoice(sensevoice_repo: str):
    """加载 SenseVoiceSmall（通过 mlx-audio），返回模型实例。"""
    from mlx_audio.stt.utils import load_model
    print(f"[模型子进程] 加载 SenseVoiceSmall: {sensevoice_repo}", flush=True)
    return load_model(sensevoice_repo)


def _warmup_sensevoice(sv_model):
    """用一段静音预热 SenseVoice，确保第一帧推理不慢。"""
    import mlx.core as mx
    dummy = mx.zeros((16000,), dtype=mx.float32)
    try:
        sv_model.generate(dummy, language="auto", verbose=False)
    except Exception:
        pass
    print("[模型子进程] SenseVoiceSmall 就绪", flush=True)


def _run_sensevoice(audio: np.ndarray, task: dict, sv_model) -> dict:
    import mlx.core as mx
    t0 = time.perf_counter()

    asr_language = task.get("asr_language") or None
    sv_lang = "auto"
    if asr_language:
        sv_lang = _SV_LANG_MAP.get(asr_language.lower(), "auto")
        print(f"[ASR/SenseVoice] 语言: {asr_language} → {sv_lang}", flush=True)

    audio_mx = mx.array(audio.astype(np.float32))
    result = sv_model.generate(audio_mx, language=sv_lang, verbose=False)

    asr_ms = round((time.perf_counter() - t0) * 1000)
    raw = _hallucination_filter((result.text or "").strip())
    sv_lang_out = result.language or ""
    lang = _SV_LANG_OUT.get(sv_lang_out, sv_lang_out.lower())
    return {"raw": raw, "lang": lang, "asr_ms": asr_ms}


# ── 子进程主函数 ──────────────────────────────────────────────

def worker_main(task_q: Queue, result_q: Queue,
                whisper_repo: str, llm_repo: str,
                sensevoice_repo: str = "", asr_backend: str = "whisper"):
    """在独立进程里加载模型并处理任务，完全无 asyncio。"""
    from mlx_lm import load as mlx_load, generate as mlx_gen
    from mlx_lm.sample_utils import make_sampler

    sv_model = None

    # ── 加载 ASR 模型 ─────────────────────────────────────────
    if asr_backend == "sensevoice" and sensevoice_repo:
        result_q.put({"type": "status", "text": "加载 SenseVoiceSmall…"})
        sv_model = _load_sensevoice(sensevoice_repo)
        result_q.put({"type": "status", "text": "预热 SenseVoiceSmall…"})
        _warmup_sensevoice(sv_model)
    else:
        import mlx_whisper
        asr_backend = "whisper"
        print(f"[模型子进程] 预热 Whisper: {whisper_repo}", flush=True)
        result_q.put({"type": "status", "text": "预热 ASR 模型…"})
        mlx_whisper.transcribe(np.zeros(16000, dtype="float32"),
                               path_or_hf_repo=whisper_repo)
        print("[模型子进程] Whisper 就绪", flush=True)

    # ── 加载 LLM ─────────────────────────────────────────────
    print(f"[模型子进程] 加载 LLM: {llm_repo}", flush=True)
    result_q.put({"type": "status", "text": "加载 LLM 中，请稍候…"})
    llm_model, llm_tok = mlx_load(llm_repo)
    print("[模型子进程] LLM 就绪，系统准备完毕", flush=True)

    result_q.put({"type": "status", "text": "就绪", "ready": True,
                  "asr_backend": asr_backend})

    sampler = make_sampler(temp=0.0)   # 贪婪解码：矫正任务不需要创造性，温度越低越忠实原文

    # ── 主循环 ───────────────────────────────────────────────
    while True:
        task = task_q.get()
        if task is None:
            break

        kind = task.get("kind")

        if kind == "asr":
            audio = np.frombuffer(task["audio_bytes"], dtype=np.float32)
            try:
                if asr_backend == "sensevoice" and sv_model is not None:
                    r = _run_sensevoice(audio, task, sv_model)
                else:
                    r = _run_whisper(audio, task, whisper_repo)
            except Exception as e:
                print(f"[ASR] 推理出错: {e}", flush=True)
                r = {"raw": "", "lang": "", "asr_ms": 0}

            result_q.put({
                "type":             "asr_done",
                "raw":              r["raw"],
                "lang":             r["lang"],
                "asr_ms":           r["asr_ms"],
                "task_id":          task.get("task_id"),
                "audio_start_time": task.get("audio_start_time"),
            })

        elif kind == "llm":
            t0 = time.perf_counter()
            # 根据任务性质动态限制 token 数：矫正输出短，翻译输出长
            prompt_str = task.get("prompt", "")
            if "minimal ASR error corrector" in prompt_str:
                max_tok = 300   # 矫正任务：输出只有 corrected + language，不需要多
            elif "multilingual translator" in prompt_str:
                max_tok = 600   # 翻译任务：多语言输出稍长
            else:
                max_tok = 800   # retranslate 等兼容任务保持原值

            # prompt 末尾已通过 assistant prefill（<think>\n</think>\n）
            # 强制 Qwen3 系列跳过 thinking 阶段直接输出 JSON，无需特殊 token 处理
            resp = mlx_gen(
                llm_model, llm_tok,
                prompt=prompt_str,
                max_tokens=max_tok,
                sampler=sampler,
                verbose=False,
            )
            llm_ms = round((time.perf_counter() - t0) * 1000)
            result_q.put({
                "type":             "llm_done",
                "resp":             resp,
                "llm_ms":           llm_ms,
                "task_id":          task.get("task_id"),
                "raw":              task.get("raw"),
                "lang":             task.get("lang"),
                "asr_ms":           task.get("asr_ms"),
                "audio_start_time": task.get("audio_start_time"),
            })


# ── 包装类 ────────────────────────────────────────────────────

class ModelWorker:
    """管理子进程生命周期的包装类"""

    def __init__(self, whisper_repo: str, llm_repo: str,
                 sensevoice_repo: str = "", asr_backend: str = "whisper"):
        self.task_q   = Queue(maxsize=12)   # ASR+LLM 任务上限，防止音频无限积压
        self.result_q = Queue(maxsize=48)   # 结果队列上限
        self._proc = Process(
            target=worker_main,
            args=(self.task_q, self.result_q,
                  whisper_repo, llm_repo,
                  sensevoice_repo, asr_backend),
            daemon=True,
        )
        self._proc.start()

    def send(self, task: dict):
        kind = task.get("kind", "")
        if kind == "retranslate":
            # retranslate 是用户主动操作，不可丢弃，阻塞等待空位（最多 2s）
            try:
                self.task_q.put(task, timeout=2)
            except Exception:
                print("[Worker] retranslate 队列满，已丢弃", flush=True)
        else:
            # ASR / LLM：队列满时丢弃队头最旧任务，保持实时性
            try:
                self.task_q.put_nowait(task)
            except Exception:
                try:
                    self.task_q.get_nowait()   # 丢掉最旧的一个
                except Exception:
                    pass
                try:
                    self.task_q.put_nowait(task)
                except Exception:
                    print(f"[Worker] {kind} 任务队列满，已丢弃", flush=True)

    def recv_nowait(self):
        try:
            return self.result_q.get_nowait()
        except Exception:
            return None

    def stop(self):
        # 先中断子进程（不等待 LLM 推理自然结束），再发退出哨兵
        # 这样退出延迟从最长 5s 降至 ~0.3s
        self._proc.terminate()
        try:
            self.task_q.put_nowait(None)   # 万一 terminate 后子进程还在读队列
        except Exception:
            pass
        self._proc.join(timeout=2)
        if self._proc.is_alive():
            self._proc.kill()
            self._proc.join(timeout=1)
        try:
            self.task_q.close()
            self.task_q.join_thread()
        except Exception:
            pass
        try:
            self.result_q.close()
            self.result_q.join_thread()
        except Exception:
            pass
