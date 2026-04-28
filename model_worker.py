"""
model_worker.py — 独立子进程，完全脱离 asyncio/uvicorn 环境
专门负责 Whisper ASR + Qwen3 LLM，通过 multiprocessing.Queue 通信
"""
import json, os, re, time, numpy as np
from multiprocessing import Process, Queue

# 强制离线模式：跳过 huggingface_hub 的网络版本检查
# 模型已在本地缓存时直接读取，冷启动速度大幅提升
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"


def worker_main(task_q: Queue, result_q: Queue, whisper_repo: str, llm_repo: str):
    """在独立进程里加载模型并处理任务，完全无 asyncio"""
    import mlx_whisper
    from mlx_lm import load as mlx_load, generate as mlx_gen
    from mlx_lm.sample_utils import make_sampler

    # ── 串行加载两个模型 ──────────────────────────────────────
    print(f"[模型子进程] 预热 Whisper: {whisper_repo}", flush=True)
    result_q.put({"type": "status", "text": "预热 ASR 模型…"})
    mlx_whisper.transcribe(np.zeros(16000, dtype="float32"), path_or_hf_repo=whisper_repo)
    print("[模型子进程] Whisper 就绪", flush=True)

    print(f"[模型子进程] 加载 LLM: {llm_repo}", flush=True)
    result_q.put({"type": "status", "text": "加载 LLM 中，请稍候…"})
    llm_model, llm_tok = mlx_load(llm_repo)
    print("[模型子进程] LLM 就绪，系统准备完毕", flush=True)

    result_q.put({"type": "status", "text": "就绪", "ready": True})

    sampler = make_sampler(temp=0.05)

    # ── 主循环：处理任务 ──────────────────────────────────────
    while True:
        task = task_q.get()
        if task is None:
            break

        kind = task.get("kind")

        if kind == "asr":
            audio = np.frombuffer(task["audio_bytes"], dtype=np.float32)
            t0 = time.perf_counter()
            initial_prompt = task.get("initial_prompt") or None
            asr_language   = task.get("asr_language") or None   # None = 自动检测
            if asr_language:
                print(f"[ASR] 语言锁定: {asr_language}", flush=True)
            res = mlx_whisper.transcribe(
                audio,
                path_or_hf_repo=whisper_repo,
                language=asr_language,             # None=自动, "zh"/"en"/"ja"...
                word_timestamps=False,
                fp16=True,
                temperature=0.0,                   # 贪婪解码，精度最高
                condition_on_previous_text=False,  # 避免上下文污染导致幻觉
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.0,
                logprob_threshold=-1.0,
                initial_prompt=initial_prompt,     # 用户场景/术语提示
            )
            asr_ms = round((time.perf_counter() - t0) * 1000)
            raw = res.get("text", "").strip()

            # 二次过滤：检测重复词幻觉（如 "nope nope nope..."）
            if raw:
                words = raw.split()
                if len(words) >= 6:
                    # 取前6个词，如果超过60%是同一个词则判定为幻觉
                    top = max(set(words[:20]), key=words[:20].count)
                    ratio = words[:20].count(top) / min(len(words), 20)
                    if ratio > 0.5:
                        print(f"[ASR] 幻觉过滤: {raw[:60]}…", flush=True)
                        raw = ""

            result_q.put({
                "type":             "asr_done",
                "raw":              raw,
                "lang":             res.get("language", ""),
                "asr_ms":           asr_ms,
                "task_id":          task.get("task_id"),
                "audio_start_time": task.get("audio_start_time"),  # 透传句子起始时刻
            })

        elif kind == "llm":
            t0 = time.perf_counter()
            resp = mlx_gen(
                llm_model, llm_tok,
                prompt=task["prompt"],
                max_tokens=512,
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
                "audio_start_time": task.get("audio_start_time"),  # 透传句子起始时刻
            })


class ModelWorker:
    """管理子进程生命周期的包装类"""

    def __init__(self, whisper_repo: str, llm_repo: str):
        self.task_q   = Queue()
        self.result_q = Queue()
        self._proc = Process(
            target=worker_main,
            args=(self.task_q, self.result_q, whisper_repo, llm_repo),
            daemon=True,
        )
        self._proc.start()

    def send(self, task: dict):
        self.task_q.put(task)

    def recv_nowait(self):
        """非阻塞获取结果，没有则返回 None"""
        try:
            return self.result_q.get_nowait()
        except Exception:
            return None

    def stop(self):
        self.task_q.put(None)
        self._proc.join(timeout=3)
        # 修复内存泄漏：超时后强制终止，防止僵尸进程占用资源
        if self._proc.is_alive():
            self._proc.terminate()
            self._proc.join(timeout=2)
        if self._proc.is_alive():
            self._proc.kill()
        # 关闭队列，释放共享内存
        self.task_q.close()
        self.result_q.close()
