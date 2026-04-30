# ASR Live

> Fully offline real-time multilingual speech recognition + LLM semantic correction + multilingual translation  
> Built exclusively for Apple Silicon — requires an M-series Mac with 24 GB RAM or more

![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon%20only-black?logo=apple)
![RAM](https://img.shields.io/badge/RAM-24%20GB%20minimum-red)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)
![MLX](https://img.shields.io/badge/MLX-0.31%2B-orange)
![Version](https://img.shields.io/badge/version-4.0f-informational)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ⚠️ Hardware Requirements

ASR Live runs two large AI models simultaneously — a Whisper ASR model and a Qwen3 LLM — entirely on-device using Apple's MLX framework. **This is not optional software that degrades gracefully on lower-spec hardware.** Both models must fit in unified memory at the same time.

| | Minimum | Recommended |
|---|---|---|
| **Chip** | Apple M1 | M2 Pro / M3 / M4 or later |
| **Unified Memory** | **24 GB** | **48 GB** |
| **Storage** | 15 GB free | 30 GB free |
| **macOS** | 13 Ventura | 14 Sonoma or later |

> **Why 24 GB?** The default model pair (whisper-large-v3-turbo ≈ 3 GB + Qwen3-14B-4bit ≈ 8 GB) consumes roughly 11–13 GB for the models alone. macOS, the UI, and working buffers need the rest. On 16 GB machines the system will thrash or crash under load. If you only have 16 GB, use a smaller LLM such as Qwen3-8B-4bit and accept reduced translation quality.

---

## Features

- **Fully offline** — Recognition, correction, and translation run entirely locally. No audio or text ever leaves your machine.
- **Native .app** — Ships as a double-click macOS application; no terminal required after initial setup.
- **Real-time subtitles** — VAD-triggered sentence segmentation delivers results in approximately 0.5–2 seconds end-to-end.
- **Three-language auto-detection** — Recognises Chinese, English, and Japanese in real time, with optional translation into Korean, French, German, and Spanish.
- **ASR language lock** — Pin Whisper to a specific language (Auto / Chinese / English / Japanese) to prevent mis-detection in monolingual sessions.
- **LLM semantic correction** — Qwen3 fixes homophones, punctuation, and domain terminology using recent conversational context.
- **Scenario and terminology prompts** — A free-text field is injected into both Whisper's `initial_prompt` and the LLM system prompt. List key terms directly for best results (see Settings section).
- **Segmented MP3 recording** — Audio is written in 5-minute segments during the session and merged into a single timestamped MP3 on stop. Pause and resume without losing audio.
- **Microphone software gain** — Boost the captured signal up to 4× directly in the app when macOS restricts the hardware mic level.
- **Subtitle playback sync** — An inline audio player lets you replay the recording while the transcript scrolls in lock-step with the audio position.
- **Search and highlight** — Filter the live transcript by keyword with real-time highlighting.
- **Dark / light themes** — One-click toggle, persisted across sessions.
- **Multi-format export** — TXT, SRT, JSON, and Markdown, with per-language filtering and a native macOS save panel.
- **Built-in model downloader** — First-run wizard detects missing models and streams download progress directly in the UI.
- **Hallucination filtering** — Whisper outputs with more than 50% repeated tokens are automatically discarded.

---

## Quick Start

### 1. Install system dependencies

```bash
# Required for MP3 encoding
brew install ffmpeg
```

### 2. Create the runtime environment

```bash
python3 -m venv ~/asr-env
source ~/asr-env/bin/activate
pip install -r requirements.txt
pip install onnxruntime pywebview
```

### 3. Download models

```bash
# ASR model — recommended (~3 GB)
huggingface-cli download mlx-community/whisper-large-v3-turbo

# ASR model — highest accuracy (~6 GB, slower)
# huggingface-cli download mlx-community/whisper-large-v3-mlx

# LLM for correction and translation (~8 GB)
huggingface-cli download mlx-community/Qwen3-14B-4bit
```

> Models are cached in `~/.cache/huggingface/hub/` and work offline once downloaded. You can also download them through the built-in guide after first launch.

### 4. Launch

```bash
source ~/asr-env/bin/activate
cd asr_app_v4_0f
python main.py
```

A native window opens automatically. The terminal will show:

```
[Worker] Warming up Whisper: mlx-community/whisper-large-v3-turbo
[Worker] Whisper ready
[Worker] Loading LLM: mlx-community/Qwen3-14B-4bit
[Worker] LLM ready — system ready
```

First startup takes approximately 30–60 seconds. Click **Start** once the status bar shows "Ready".

---

## Packaging as a .app

```bash
# 1. Install Python 3.11 for packaging (separate from the runtime)
brew install pyenv
pyenv install 3.11.9

# 2. Create a dedicated build environment (one-time)
cd asr_app_v4_0f
~/.pyenv/versions/3.11.9/bin/python -m venv venv_build
source venv_build/bin/activate
pip install py2app

# 3. Build
rm -rf build dist
python build_mac.py py2app

# 4. Install
open dist/
# Drag "ASR Live.app" to /Applications
```

The `.app` bundle is approximately 15 MB and does not embed MLX, PyTorch, or any AI models. All large dependencies stay in `~/asr-env`.

**First-launch Gatekeeper warning:**

```bash
xattr -cr "/Applications/ASR Live.app"
```

Or right-click → Open → click "Open" in the dialog.

---

## Settings

### ASR model

Automatically lists all locally cached Whisper models. Switch between whisper-large-v3-turbo (faster) and whisper-large-v3 (more accurate) without restarting.

### LLM correction model

Automatically lists locally cached MLX-format LLMs — Qwen3, LLaMA, Gemma, Mistral, and others.

### Scenario and terminology

Enter domain background and vocabulary for the current session. The same text is applied simultaneously to both models:

- **Whisper `initial_prompt`** — Biases acoustic decoding toward listed terms.
- **LLM system prompt** — Guides semantic correction toward the correct spelling of domain terms.

**Listing key terms directly produces better results than describing the scene in prose:**

```text
# Good
Patent hearing. Terms: claims, specification, novelty, inventive step, prior art, dependent claims

# Good
Medical consultation. Terms: atrial fibrillation, LVEF, CABG, ejection fraction, sinus rhythm

# Less effective
This is a meeting about patents and medical topics.
```

### ASR language lock

Select **Auto** to let Whisper detect the language on each sentence, or pin to **Chinese / English / Japanese** for monolingual sessions. The lock cannot be changed while recording is active.

### Translation targets

Choose which languages to display as translations. The language currently being spoken is automatically excluded from translation output to avoid duplication.

### Audio input

| Parameter | Default | Range | Notes |
|---|---|---|---|
| Microphone gain | 1.5× | 1.0–4.0× | Software boost applied before VAD and Whisper |
| End-of-sentence silence | 0.6 s | 0.2–2.0 s | Pause duration that triggers sentence segmentation |
| VAD sensitivity | 0.45 | 0.20–0.80 | Higher = less sensitive; increase to 0.6–0.7 in noisy environments |
| Maximum utterance length | 20 s | — | Forces segmentation if a sentence exceeds this duration |
| Save recording | Enabled | — | Disable for transcription-only mode with no files written |
| MP3 bitrate | 192 kbps | 64 / 128 / 192 / 320 | Applied to the merged final file |
| Microphone device | System default | — | Supports AirPods, USB mics; click ↻ to rescan |

---

## Recording

- Audio is buffered in 5-minute segments during the session to bound memory usage.
- **Pause** temporarily stops the microphone stream while keeping the current session open. **Resume** appends to the same MP3.
- After **Stop**, segments are merged in the background into a single file: `ASRLive_YYYYMMDD_HHMMSS.mp3`, saved to `~/Downloads` by default. A native save panel appears in windowed mode.
- The **playback bar** appears automatically once the file is ready. It supports play/pause, scrubbing, volume control, and a "follow playback" mode that scrolls the transcript in sync.
- Each transcript entry stores its precise start offset in the MP3. Clicking an entry in follow mode jumps the player to that sentence.
- On exit, the application waits up to 30 seconds for any pending encoding to finish before quitting.

---

## Export

Click **Export ▾** in the top bar, choose a language filter and format, then save via the native panel.

| Format | Description |
|---|---|
| TXT | Plain text with a timestamp per entry |
| SRT | Standard subtitle format, importable into video editors |
| JSON | Full detail: timestamps, latency, raw ASR, corrected text, all translations |
| Markdown | Suitable for Obsidian, Notion, or any Markdown-based tool |

**Language filter options:** all languages mixed · corrected original only · raw ASR only · any single language (includes entries where that language appears as either the original or a translation).

For long sessions, transcript history beyond the most recent 200 entries is streamed to a JSONL file in `~/Downloads` and automatically included in exports.

---

## FAQ

**The window shows "Connecting" for a long time after launch.**  
The first load takes 30–60 seconds while both models are read into unified memory. Wait until the status bar shows "Ready" before interacting.

**`Address already in use` on startup.**  
`main.py` automatically kills any process holding port 17433 on launch. If the problem persists: `lsof -ti :17433 | xargs kill -9`

**Microphone permission error: `PortAudioError -9986`.**  
System Settings → Privacy & Security → Microphone → grant permission to Terminal or ASR Live.app.

**Repeated hallucinations like "nope nope nope…".**  
The app filters these automatically. If they persist, raise VAD sensitivity to 0.6–0.7 and ensure the silence threshold is at least 0.5 s.

**No translation output.**  
Confirm that translation target languages are selected and saved in Settings. If the issue persists, delete `~/.asrlive_settings.json` and restart.

**LLM latency is too high (>3 s).**  
Switch to a smaller model (Qwen3-8B-4bit) or reduce the number of translation targets. Each additional target language adds one JSON field for the LLM to generate.

**Recording was not saved.**  
Confirm ffmpeg is installed (`brew install ffmpeg`) and that "Save recording" is enabled in Settings.

**`No module named 'onnxruntime'`.**  
`pip install onnxruntime`

**Model downloads are slow or fail.**  
Use the Hugging Face mirror: `export HF_ENDPOINT=https://hf-mirror.com`

---

## Dependencies

| Project | Purpose |
|---|---|
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | Native Whisper inference on Apple Silicon via MLX |
| [mlx-lm](https://github.com/ml-explore/mlx-examples) | Native LLM inference on Apple Silicon via MLX |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Voice activity detection |
| [FastAPI](https://fastapi.tiangolo.com) | Backend API and WebSocket server |
| [pywebview](https://pywebview.flowrl.com) | Native macOS window (WKWebView) |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Microphone capture via PortAudio |
| [ffmpeg](https://ffmpeg.org) | MP3 encoding for recordings |
| [Qwen3](https://huggingface.co/Qwen) | LLM for semantic correction and translation |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | Default ASR model |

---

## License

MIT
