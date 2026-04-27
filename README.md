# ASR Live

> Fully offline real-time multilingual Automatic Speech Recognition (ASR) + LLM-based semantic correction + multilingual translation  
> Optimized for Apple Silicon Macs, based on the native MLX framework, and designed to run completely offline

![Platform](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)
![MLX](https://img.shields.io/badge/MLX-0.31%2B-orange)
![Version](https://img.shields.io/badge/version-3.0-informational)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

- **Fully offline** — Speech recognition, correction, and translation all run locally; no data is uploaded, and the application works normally without an internet connection.
- **Native .app** — Packaged as a standalone macOS application; double-click to launch, with no terminal required.
- **Real-time near-synchronous subtitles** — Recognizes speech immediately after VAD detects an end-of-sentence pause, with an overall latency of approximately 0.5–2 seconds.
- **Automatic detection of three languages** — Automatically recognizes Chinese, English, and Japanese, and also supports translation into Korean, French, German, and Spanish.
- **Intelligent translation switching** — When Chinese is spoken, English and Japanese translations are displayed; when English is spoken, Chinese and Japanese translations are displayed. The same language is not repeated.
- **LLM-based semantic correction** — Qwen3 corrects typos, specialized terminology, and punctuation, and uses context to improve accuracy.
- **Scenario and terminology prompts** — Enter domain descriptions and specialized terms to improve the recognition accuracy of both Whisper and the LLM.
- **Hallucination filtering** — Automatically detects and discards repetitive hallucinated outputs from Whisper.
- **Synchronized recording** — Records throughout the session. After stopping, a native save dialog appears. Supports MP3 at 64–320 kbps.
- **Search highlighting** — Filters subtitles in real time and highlights keywords, with a one-click clear button.
- **Dark / light themes** — One-click switching, with preferences automatically persisted.
- **Multi-format export** — TXT, SRT, JSON, and Markdown exports, with native save panels and optional export by language.
- **Dynamic model selection** — Automatically scans locally downloaded models; no manual path entry required.
- **First-time download guide** — If models are missing, the built-in guide opens automatically and displays real-time download progress.

---

## System Requirements

| Item | Requirement |
|------|-------------|
| Hardware | Apple Silicon Mac (M1 or later) |
| Memory | 24 GB+ recommended; 48 GB recommended for keeping large models resident at the same time |
| Operating system | macOS 13 Ventura or later |
| Python | 3.11–3.14 for runtime / 3.11 for packaging |
| ffmpeg | Required for MP3 recording encoding |

---

## Quick Start

### 1. Install dependencies

```bash
# Install ffmpeg (required for recording)
brew install ffmpeg

# Create a virtual environment
python3 -m venv ~/asr-env
source ~/asr-env/bin/activate

# Install Python dependencies
pip install -r requirements.txt
pip install onnxruntime pywebview
```

### 2. Download models

```bash
# ASR model (recommended, approximately 3 GB)
hf download mlx-community/whisper-large-v3-turbo

# Or the highest-accuracy version (approximately 6 GB)
hf download mlx-community/whisper-large-v3-mlx

# LLM model for correction and translation (approximately 8 GB)
hf download mlx-community/Qwen3-14B-4bit
```

> Models are cached in `~/.cache/huggingface/hub/`. Once downloaded, they can be used offline permanently.  
> You can also download them through the built-in guide after launching the application.

### 3. Launch the application

```bash
source ~/asr-env/bin/activate
cd asr_app_v3
python main.py
```

After launch, a native window opens automatically. The terminal will display the following messages in sequence:

```text
[模型子进程] 预热 Whisper: mlx-community/whisper-large-v3-turbo
[模型子进程] Whisper 就绪
[模型子进程] 加载 LLM: mlx-community/Qwen3-14B-4bit
[模型子进程] LLM 就绪，系统准备完毕
```

After “ready” appears, click “Start Recognition” to begin. The first startup usually takes approximately 30–60 seconds.

---

## Packaging as a .app

After packaging, the application can be launched by double-clicking, with no terminal required.

### Steps

```bash
# 1. Install Python 3.11 for packaging, separate from the runtime environment
brew install pyenv
pyenv install 3.11.9

# 2. Create a dedicated packaging venv; this only needs to be done once
cd asr_app_v3
~/.pyenv/versions/3.11.9/bin/python -m venv venv_build
source venv_build/bin/activate
pip install py2app

# 3. Package the application
rm -rf build dist
python build_mac.py py2app

# 4. Install
open dist/
# Drag "ASR Live.app" into the Applications folder
```

### How it works

```text
Double-click ASR Live.app
  → launcher.py (Python 3.11, lightweight launcher)
  → automatically locates ~/asr-env/bin/python3
  → launches main.py (Python 3.14 + full dependencies)
  → opens the native window
```

The .app itself is approximately 15 MB and does not embed large dependencies such as MLX or PyTorch. All AI models and libraries remain in `~/asr-env`.

### First-time warning: “Apple cannot verify the developer”

```bash
xattr -cr /Applications/ASR\ Live.app
```

Alternatively, right-click → Open → click “Open” in the dialog.

---

## Settings

### ASR model

The application automatically lists locally downloaded Whisper models, including large-v3-turbo and large-v3.

### LLM correction model

The application automatically lists locally downloaded LLMs in MLX format, including Qwen3, LLaMA, Gemma, and Mistral.

### Scenario and terminology

Enter the domain background and specialized vocabulary for the current recording. This applies **simultaneously** to both models:

- **Whisper `initial_prompt`** — Improves acoustic recognition accuracy for specialized terms.
- **LLM system prompt** — Improves terminology correction during semantic correction.

Examples:

```text
This is a cardiology consultation dialogue involving: atrial fibrillation, left ventricular ejection fraction, coronary artery bypass grafting
```

```text
Tech podcast about Apple. Key terms: Neural Engine, MLX, M3 Max, unified memory
```

### Translation targets

Select the languages to output; multiple languages may be selected. The language currently being spoken is automatically excluded from the translation output to avoid duplicate display.

### Audio input

| Parameter | Default | Description |
|-----------|---------|-------------|
| Save recording | Enabled | A native save dialog appears when recognition stops. |
| MP3 bitrate | 192 kbps | Options: 64 / 128 / 192 / 320 kbps |
| End-of-sentence silence threshold | 0.6 s | Duration of a pause required to treat speech as a complete sentence; adjustable from 0.2–2.0 s |
| VAD sensitivity | 0.45 | Higher values make VAD less sensitive. In noisy environments, increase to 0.6–0.7. |
| Maximum duration per utterance | 20 s | Recognition is forcibly segmented if this duration is exceeded. |
| Microphone device | System default | Supports AirPods, USB microphones, etc. Click ↻ to refresh. |

---

## Export

Click “Export ▾” in the top bar, select a language filter and format, and then use the native save panel:

| Format | Description |
|--------|-------------|
| TXT | Plain text, with a timestamp for each entry |
| SRT | Standard subtitle format, importable into video editing software |
| JSON | Includes complete timestamps, latency, original text, and all translations |
| Markdown | Suitable for pasting into note-taking tools such as Obsidian or Notion |

**Language filter**: all mixed, original only (corrected), original only (raw ASR), or any single language. When a single language is selected, entries where that language appears either as the original text or as a translation will be included.

---

## Recording

- All audio, including silent segments, is recorded synchronously while recognition is running.
- After clicking “Stop Recognition,” a native macOS save panel appears for selecting the save location.
- Default filename: `ASRLive_YYYYMMDD_HHMMSS.mp3`
- If there is an unsaved recording when the application exits, the application automatically waits for encoding to finish, for up to 30 seconds.

---

## FAQ

**Q: The window keeps showing “Connecting” for a long time after launch.**  
A: The first load takes 30–60 seconds while the models are loaded into memory. Wait until the terminal shows “LLM ready” before operating the application.

**Q: The port is occupied: `Address already in use`.**  
A: `main.py` automatically cleans up old processes during startup, so manual handling is usually unnecessary. If the issue persists, run: `lsof -ti :17433 | xargs kill -9`

**Q: Microphone permission error: `PortAudioError -9986`.**  
A: Go to System Settings → Privacy & Security → Microphone, and confirm that Terminal / ASR Live.app has been granted permission.

**Q: A large number of repeated words appear, such as “nope nope nope…”.**  
A: This is a Whisper hallucination. The application has built-in filtering. If it still occurs, increase VAD sensitivity to 0.6–0.7.

**Q: No translation output appears.**  
A: Confirm that translation target languages have been selected and saved in the settings panel. If the issue persists, delete `~/.asrlive_settings.json` and restart.

**Q: LLM latency is too long (>2 s).**  
A: Use a smaller model, such as Qwen3-8B-4bit, or reduce the number of translation target languages.

**Q: The recording was not saved.**  
A: Confirm that ffmpeg has been installed (`brew install ffmpeg`) and that recording is enabled in the settings.

**Q: `No module named 'onnxruntime'`.**  
A: Run `pip install onnxruntime`.

**Q: Model downloads are very slow or fail.**  
A: Use the Hugging Face mirror: `export HF_ENDPOINT=https://hf-mirror.com`

---

## Dependencies

| Project | Purpose |
|---------|---------|
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | Native Whisper inference on Apple Silicon |
| [mlx-lm](https://github.com/ml-explore/mlx-examples) | Native LLM inference on Apple Silicon |
| [Silero VAD](https://github.com/snakers4/silero-vad) | Voice activity detection |
| [FastAPI](https://fastapi.tiangolo.com) | Backend API and WebSocket |
| [pywebview](https://pywebview.flowrl.com) | Native macOS window (WKWebView) |
| [sounddevice](https://python-sounddevice.readthedocs.io) | Audio capture |
| [ffmpeg](https://ffmpeg.org) | MP3 recording encoding |
| [Qwen3](https://huggingface.co/Qwen) | LLM for semantic correction and translation |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | Base ASR model |

---

## License

MIT
