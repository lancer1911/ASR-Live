# ASR Live

> 全离线实时多语言语音识别 + LLM 语义矫正 + 多语言翻译  
> 专为 Apple Silicon Mac 优化，基于 MLX 原生框架，完全不联网

![Platform](https://img.shields.io/badge/platform-macOS-lightgrey?logo=apple)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)
![MLX](https://img.shields.io/badge/MLX-0.31%2B-orange)
![Version](https://img.shields.io/badge/version-3.0-informational)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 功能特性

- **全程离线** — 语音识别、矫正、翻译均在本地运行，无数据上传，断网也能正常使用
- **原生 .app** — 打包为独立 macOS 应用，双击启动，无需终端
- **实时近同步字幕** — VAD 检测句尾停顿后立即识别，总时滞约 0.5–2 秒
- **三语自动检测** — 中文、英文、日文自动识别，同时支持韩、法、德、西班牙文翻译
- **智能翻译切换** — 说中文时显示英日译文，说英文时切换为中日，同语言不重复
- **LLM 语义矫正** — Qwen3 修正错别字、专业术语、标点，利用上下文提升准确率
- **场景术语提示** — 输入领域描述和专业词汇，同时增强 Whisper 和 LLM 的识别精度
- **幻觉过滤** — 自动检测并丢弃 Whisper 的重复词幻觉输出
- **同步录音** — 全程同步录制，停止后弹出原生保存对话框，支持 64–320kbps MP3
- **搜索高亮** — 实时过滤字幕，关键词高亮显示，含一键清除按钮
- **深色 / 浅色主题** — 一键切换，偏好自动持久化
- **多格式导出** — TXT、SRT、JSON、Markdown，可按语言单独导出，原生保存面板
- **动态模型选择** — 自动扫描本地已下载模型，无需手动填写路径
- **首次下载引导** — 缺少模型时自动弹出引导界面，实时显示下载进度

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 硬件 | Apple Silicon Mac（M1 及以上） |
| 内存 | 建议 24GB+，推荐 48GB（可同时常驻大模型） |
| 系统 | macOS 13 Ventura 及以上 |
| Python | 3.11 – 3.14（运行时）/ 3.11（打包用） |
| ffmpeg | 用于 MP3 录音编码 |

---

## 快速开始

### 1. 安装依赖

```bash
# 安装 ffmpeg（录音功能需要）
brew install ffmpeg

# 创建虚拟环境
python3 -m venv ~/asr-env
source ~/asr-env/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
pip install onnxruntime pywebview
```

### 2. 下载模型

```bash
# ASR 模型（推荐，约 3GB）
hf download mlx-community/whisper-large-v3-turbo

# 或最高精度版本（约 6GB）
hf download mlx-community/whisper-large-v3-mlx

# LLM 矫正与翻译模型（约 8GB）
hf download mlx-community/Qwen3-14B-4bit
```

> 模型缓存在 `~/.cache/huggingface/hub/`，下载一次永久离线可用。  
> 也可以启动应用后通过内置引导界面下载。

### 3. 启动应用

```bash
source ~/asr-env/bin/activate
cd asr_app_v3
python main.py
```

启动后自动弹出原生窗口。终端会依次显示：

```
[模型子进程] 预热 Whisper: mlx-community/whisper-large-v3-turbo
[模型子进程] Whisper 就绪
[模型子进程] 加载 LLM: mlx-community/Qwen3-14B-4bit
[模型子进程] LLM 就绪，系统准备完毕
```

等出现「就绪」后点击「开始识别」即可（首次约 30–60 秒）。

---

## 打包为 .app

打包后双击即可启动，无需终端。

### 步骤

```bash
# 1. 安装 Python 3.11（打包用，与运行环境分离）
brew install pyenv
pyenv install 3.11.9

# 2. 建立打包专用 venv（只需一次）
cd asr_app_v3
~/.pyenv/versions/3.11.9/bin/python -m venv venv_build
source venv_build/bin/activate
pip install py2app

# 3. 打包
rm -rf build dist
python build_mac.py py2app

# 4. 安装
open dist/
# 把 "ASR Live.app" 拖到 Applications 文件夹
```

### 运行原理

```
双击 ASR Live.app
  → launcher.py（Python 3.11，轻量启动器）
  → 自动定位 ~/asr-env/bin/python3
  → 启动 main.py（Python 3.14 + 完整依赖）
  → 弹出原生窗口
```

.app 本身约 15MB，不内嵌 MLX/PyTorch 等大型依赖，所有 AI 模型和库保留在 `~/asr-env`。

### 首次打开提示「无法验证开发者」

```bash
xattr -cr /Applications/ASR\ Live.app
```

或右键 → 打开 → 在弹窗中点「打开」。

---


## 设置说明

### ASR 模型
从本地已下载的 Whisper 模型中自动列出，支持 large-v3-turbo、large-v3 等。

### LLM 矫正模型
从本地已下载的 LLM 中自动列出，支持 Qwen3、LLaMA、Gemma、Mistral 等 MLX 格式。

### 场景与术语
输入当前录音的领域背景和专业词汇，**同时**作用于两个模型：
- **Whisper `initial_prompt`** — 提升专业词汇的声学识别准确率
- **LLM system prompt** — 提升语义矫正时的术语纠错能力

示例：
```
这是心血管科会诊对话，涉及：心房颤动、左心室射血分数、冠状动脉旁路移植术
```
```
Tech podcast about Apple. Key terms: Neural Engine, MLX, M3 Max, unified memory
```

### 翻译目标
勾选需要输出的语言（可多选）。当前说的语言自动从译文中排除，避免重复显示。

### 音频输入

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 录音保存 | 开启 | 停止时弹出原生保存对话框 |
| MP3 码率 | 192kbps | 可选 64 / 128 / 192 / 320kbps |
| 句尾静音阈值 | 0.6s | 停顿多久后认为一句话结束，可调 0.2–2.0s |
| VAD 灵敏度 | 0.45 | 越高越不敏感，嘈杂环境建议调高至 0.6–0.7 |
| 最长单句时长 | 20s | 超过此时长强制切断识别 |
| 麦克风设备 | 系统默认 | 支持 AirPods、USB 麦克风等，点 ↻ 刷新 |

---

## 导出

点击顶栏「导出 ▾」，选择语言筛选和格式后弹出原生保存面板：

| 格式 | 说明 |
|------|------|
| TXT | 纯文本，每条含时间戳 |
| SRT | 标准字幕格式，可导入视频软件 |
| JSON | 含完整时间戳、时滞、原文、所有译文 |
| Markdown | 适合粘贴到 Obsidian、Notion 等笔记工具 |

**语言筛选**：全部混合、仅原文（矫正后）、仅原文（ASR 原始）、或任意单一语言。选择单一语言时，该语言作为原文或译文的条目均会包含。

---

## 录音

- 开始识别时全程同步录制所有音频（含静音段）
- 点击「停止识别」后弹出原生 macOS 保存面板选择保存位置
- 默认文件名：`ASRLive_YYYYMMDD_HHMMSS.mp3`
- 应用退出时若有未保存录音，自动等待编码完成（最多 30 秒）

---

## 常见问题

**Q: 启动后窗口长时间显示「连接中」？**  
A: 首次加载需要 30–60 秒（模型加载到内存），等终端出现「LLM 就绪」再操作。

**Q: 端口被占用 `Address already in use`？**  
A: `main.py` 启动时会自动清理旧进程，无需手动处理。若仍出现：`lsof -ti :17433 | xargs kill -9`

**Q: 麦克风权限错误 `PortAudioError -9986`？**  
A: 系统设置 → 隐私与安全性 → 麦克风，确认 Terminal / ASR Live.app 已授权。

**Q: 出现大量重复词「nope nope nope…」？**  
A: Whisper 幻觉，程序已内置过滤。若仍出现，调高 VAD 灵敏度至 0.6–0.7。

**Q: 没有翻译输出？**  
A: 确认设置面板中已勾选翻译目标语言并保存。如仍无效，删除 `~/.asrlive_settings.json` 后重启。

**Q: LLM 时滞过长（>2s）？**  
A: 换用更小模型（Qwen3-8B-4bit），或减少翻译目标语言数量。

**Q: 录音没有保存？**  
A: 确认已安装 ffmpeg（`brew install ffmpeg`），且设置中录音开关为开启状态。

**Q: `No module named 'onnxruntime'`？**  
A: `pip install onnxruntime`

**Q: 模型下载很慢或失败？**  
A: 使用 HF 镜像：`export HF_ENDPOINT=https://hf-mirror.com`

---

## 依赖项目

| 项目 | 用途 |
|------|------|
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | Apple Silicon 原生 Whisper 推理 |
| [mlx-lm](https://github.com/ml-explore/mlx-examples) | Apple Silicon 原生 LLM 推理 |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 语音活动检测 |
| [FastAPI](https://fastapi.tiangolo.com) | 后端 API 和 WebSocket |
| [pywebview](https://pywebview.flowrl.com) | 原生 macOS 窗口（WKWebView） |
| [sounddevice](https://python-sounddevice.readthedocs.io) | 音频采集 |
| [ffmpeg](https://ffmpeg.org) | MP3 录音编码 |
| [Qwen3](https://huggingface.co/Qwen) | 语义矫正和翻译 LLM |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | ASR 基础模型 |

---

## License

MIT
