> 🌐 [English](README.md)

# Lancer1911 ASR Live

> 完全离线的实时多语言语音识别 + LLM 语义矫正 + 多语言翻译  
> 专为 Apple Silicon 设计 — 需要 M 系列 Mac，24 GB 内存或以上

![Platform](https://img.shields.io/badge/platform-macOS%20Apple%20Silicon%20only-black?logo=apple)
![RAM](https://img.shields.io/badge/RAM-24%20GB%20minimum-red)
![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)
![MLX](https://img.shields.io/badge/MLX-0.31%2B-orange)
![Version](https://img.shields.io/badge/version-4.4l-informational)
![License](https://img.shields.io/badge/license-MIT-green)

---

## ⚠️ 硬件要求

Lancer1911 ASR Live 最多同时运行三个大型 AI 模型 —— Whisper ASR 模型、Qwen3 LLM，以及可选的 pyannote 说话人声纹模型 —— 全部在本地设备运行。**本软件对低配置硬件没有降级模式。** 所有激活的模型必须同时装入统一内存。

|  | 最低配置 | 推荐配置 |
|---|---|---|
| **芯片** | Apple M1 | M2 Pro / M3 / M4 或更新 |
| **统一内存** | **24 GB** | **48 GB** |
| **存储空间** | 15 GB 可用 | 30 GB 可用（+ 0.5 GB 说话人模型） |
| **macOS 版本** | 13 Ventura | 14 Sonoma 或更新 |

> **为什么需要 24 GB？** 默认配置（whisper-large-v3-turbo ≈ 3 GB + Qwen3-14B-4bit ≈ 8 GB + pyannote 说话人模型 ≈ 0.5 GB）仅模型部分就需要约 12–14 GB。macOS、界面和工作缓冲需要其余空间。16 GB 设备在负载下会严重卡顿甚至崩溃。如果只有 16 GB，请改用 Qwen3-8B-4bit，并接受较低的翻译质量。pyannote 说话人模型为可选项，可不安装以节省内存。

---

## 截图

<p align="center">
  <img src="images/screenshot_main.png" alt="主界面 — 实时转录与翻译" width="800">
  <br><em>主界面 — 实时转录与多语言翻译（中文界面）</em>
</p>

<p align="center">
  <img src="images/screenshot_main_en.png" alt="主界面 — 英文界面" width="800">
  <br><em>主界面 — 英文界面</em>
</p>

<p align="center">
  <img src="images/screenshot_settings.png" alt="设置面板" width="480">
  <br><em>设置面板 — ASR 引擎选择、模型、场景提示词与音频配置</em>
</p>

<p align="center">
  <img src="images/screenshot_playback.png" alt="回放栏与转录同步" width="800">
  <br><em>回放栏 — 录音回放与转录文字同步滚动</em>
</p>

<p align="center">
  <img src="images/installation.png" alt="macOS 安装界面" width="600">
  <br><em>macOS 安装界面</em>
</p>

---

## 功能特性

- **完全离线** — 识别、矫正和翻译全部在本地运行，语音和文字数据不会离开你的 Mac。
- **原生 .app** — 以双击即用的 macOS 应用程序形式发布，初始安装后无需使用终端。
- **双 ASR 引擎** — 可在 **Whisper**（高精度，支持 99 种语言）和 **SenseVoice**（速度快 3–5 倍，支持情绪/事件检测，中英日韩）之间选择，两者可同时安装、在设置中切换。
- **实时字幕** — 基于语音活动检测（VAD）的句子分割，端到端延迟约 0.5–2 秒。
- **三语言自动检测** — 实时识别中文、英文、日文，并可选翻译为韩文、法文、德文、西班牙文。
- **ASR 语言锁定** — 将 ASR 引擎固定为特定语言（自动 / 中文 / 英文 / 日文），避免单语种会话中的误检测。
- **LLM 语义矫正** — Qwen3 基于近期对话上下文修正同音字、标点和专业术语。
- **场景与术语提示词** — 自由文本字段同时注入 Whisper 的 `initial_prompt` 和 LLM 系统提示词，直接列出关键术语效果最佳（详见设置说明）。
- **ModelScope / 普通目录模型兼容** — 通过 ModelScope 下载、或直接存放于缓存目录根层（非 `snapshots/` 子目录）的模型权重，可被自动识别和加载，无需任何路径配置。
- **分段 MP3 录音** — 录音期间每 5 分钟写入一段，停止时合并为单个带时间戳的 MP3 文件。暂停和继续不丢失音频。
- **麦克风软件增益** — 当 macOS 限制硬件麦克风音量时，可在应用内直接将信号提升最多 4 倍。
- **字幕回放同步** — 内置音频播放器支持录音回放，转录文字随音频位置同步滚动。
- **关键词搜索高亮** — 实时高亮过滤转录文字。
- **深色 / 浅色主题** — 一键切换，跨会话保留设置。
- **多格式导出** — 支持 TXT、SRT、JSON 和 Markdown，可按语言筛选，调用原生 macOS 保存面板。
- **内置模型下载器** — 首次运行向导自动检测缺失模型，并在界面中直接显示下载进度。
- **幻觉过滤** — ASR 输出中重复 token 超过 50% 的内容自动丢弃。
- **说话人日志** — 使用 pyannote-audio 声纹 embedding 自动识别最多 4 位说话人。每张字幕卡片显示彩色说话人标签，可重命名，可手动修正，发言人信息随所有导出格式一并输出。

---

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/lancer1911/ASR-Live.git
cd ASR-Live
```

### 2. 安装系统依赖

```bash
# MP3 编码所需
brew install ffmpeg
```

### 3. 创建运行环境

```bash
python3 -m venv ~/asr-env
source ~/asr-env/bin/activate
pip install -r requirements.txt
pip install onnxruntime pywebview
```

### 4. 下载 AI 模型

**方案 A — Whisper（默认，99 种语言）**

```bash
# 推荐 — 快速 turbo 模型（约 3 GB）
hf download mlx-community/whisper-large-v3-turbo

# 可选 — 最高精度，速度较慢（约 3 GB）
# hf download mlx-community/whisper-large-v3-mlx
```

**方案 B — SenseVoice（更快，支持中英日韩）**

```bash
pip install mlx-audio
hf download mlx-community/SenseVoiceSmall
```

> 两种引擎可同时安装，在**设置 → 选择 ASR 家族**中切换。

**LLM 矫正与翻译模型**

```bash
# 默认（约 8 GB）
hf download mlx-community/Qwen3-14B-4bit

# 高质量可选版本 — 需要 ≥48 GB 统一内存（约 16 GB）
# hf download mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit
```

> 模型缓存于 `~/.cache/huggingface/hub/`，下载后完全离线可用。也可在首次启动后通过内置向导下载。

> **ModelScope 用户：** 通过 ModelScope 下载的模型会被自动识别，无需任何路径配置。

### 5. 说话人识别模型（可选）

安装后应用自动识别最多 4 位发言人并在字幕卡片上标注。

**第一步 — 创建 HuggingFace Read Token**

前往 [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)，创建类型为 **Read**（非 fine-grained）的 Token。

**第二步 — 同意模型协议**

登录 HuggingFace 网页，分别进入以下页面点击「Agree and access repository」：

- [pyannote/embedding](https://huggingface.co/pyannote/embedding)
- [pyannote/segmentation-3.0](https://huggingface.co/pyannote/segmentation-3.0)

**第三步 — 登录并下载**

```bash
source ~/asr-env/bin/activate
pip install pyannote.audio torch omegaconf
hf auth login   # 粘贴 Read Token
hf download pyannote/embedding
hf download pyannote/segmentation-3.0
```

安装完成后应用启动时自动检测，左侧边栏显示**发言人识别：开启**即生效。也可通过**设置 → 检查发言人识别功能**查看应用内引导。

### 6. 启动

```bash
source ~/asr-env/bin/activate
cd ASR-Live
python main.py
```

原生窗口自动打开。首次启动约需 30–60 秒加载模型，状态栏显示「就绪」后点击**开始**即可。

---

## 打包为 .app

```bash
# 1. 安装 Python 3.11 用于打包（与运行环境分开）
brew install pyenv
pyenv install 3.11.9

# 2. 创建专用构建环境（仅需一次）
cd ASR-Live
~/.pyenv/versions/3.11.9/bin/python -m venv venv_build
source venv_build/bin/activate
pip install py2app

# 3. 构建
rm -rf build dist
python build_mac.py py2app

# 4. 安装
open dist/
# 将 "Lancer1911 ASR Live.app" 拖入 /Applications
```

`.app` 包体约 15 MB，不内嵌 MLX、PyTorch 或任何 AI 模型，所有大型依赖保留在 `~/asr-env`。

**首次启动 Gatekeeper 警告：**

```bash
xattr -cr "/Applications/ASR Live.app"
```

或右键 → 打开 → 在对话框中点击「打开」。

---

## 制作 DMG 分发包

需要 [create-dmg](https://github.com/create-dmg/create-dmg)：`brew install create-dmg`

>将已构建的 `.app` 和说明书 PDF 放入 `~/Desktop/ASR_Live_DMG_src/`，然后运行：

```bash
create-dmg \
  --volname "Lancer1911 ASR Live" \
  --volicon ~/Playground/asr_app_v4_0/icon.icns \
  --background ~/Desktop/ASR_Live_DMG_src/dmg_background_900x556.png \
  --window-pos 200 120 \
  --window-size 900 605 \
  --icon-size 120 \
  --icon "Lancer1911 ASR Live.app" 213 299 \
  --icon "Lancer1911_ASR_Live_Guide_EN_ZH.pdf" 450 451 \
  --hide-extension "Lancer1911 ASR Live.app" \
  --app-drop-link 683 299 \
  --disk-image-size 300 \
  ~/Desktop/ASR_Live_DMG_src/"Lancer1911 ASR Live.dmg" \
  ~/Desktop/ASR_Live_DMG_src/
```

完成后 `Lancer1911 ASR Live.dmg` 保存于 `~/Desktop/ASR_Live_DMG_src/`。

---

## 设置说明

### ASR 引擎

在**选择 ASR 家族**中选择 **Whisper** 或 **SenseVoice**，下方模型下拉框自动更新为本地已缓存的对应模型。切换在下次录音开始时生效（未录音时立即生效）。

| 引擎 | 速度 | 支持语言 | 备注 |
|---|---|---|---|
| Whisper large-v3-turbo | ~1–2 秒/句 | 99 种 | 默认；多语言会话首选 |
| SenseVoice Small | ~0.3–0.5 秒/句 | 中英日韩粤 | 需安装 `mlx-audio`；不支持 `initial_prompt` |

### LLM 矫正模型

自动列出本地缓存的 MLX 格式 LLM，支持 Qwen3、LLaMA、Gemma、Mistral 等。

### 场景与术语

为当前会话输入领域背景和词汇，同时应用于两个模型：

- **Whisper `initial_prompt`** — 将声学解码偏向所列术语。（SenseVoice 不支持此参数。）
- **LLM 系统提示词** — 引导语义矫正使用正确的专业术语拼写。

**直接列出关键术语效果优于描述场景：**

```text
# 推荐
专利听证。术语：权利要求、说明书、新颖性、创造性、现有技术、从属权利要求

# 推荐
医疗会诊。术语：心房颤动、左心室射血分数、冠状动脉旁路移植术、窦性心律

# 效果较差
这是一个关于专利和医疗的会议。
```

### ASR 语言锁定

选择**自动**让 ASR 引擎逐句检测语言，或固定为**中文 / 英文 / 日文**用于单语种会话。录音进行中不可更改。

### 翻译目标语言

选择要显示的译文语言。当前说话语言自动从译文中排除以避免重复。

### 音频输入

| 参数 | 默认值 | 范围 | 说明 |
|---|---|---|---|
| 麦克风增益 | 1.5× | 1.0–4.0× | 在 VAD 和 ASR 之前应用的软件增益 |
| 句尾静音阈值 | 0.8 s | 0.2–2.0 s | 触发句子分割的停顿时长 |
| VAD 灵敏度 | 0.40 | 0.20–0.80 | 越高越不灵敏；嘈杂环境建议调至 0.6–0.7 |
| 最长单句时长 | 20 s | — | 超过此时长强制分割 |
| 录音保存 | 开启 | — | 关闭则为纯转录模式，不写入文件 |
| MP3 码率 | 192 kbps | 64 / 128 / 192 / 320 | 应用于最终合并文件 |
| 麦克风设备 | 系统默认 | — | 支持 AirPods、USB 麦克风；点击 ↻ 重新扫描 |

### 说话人识别

需要可选的 pyannote 模型（见快速开始第 5 节）。激活后侧边栏显示彩色说话人列表。

| 参数 | 默认值 | 范围 | 说明 |
|---|---|---|---|
| 声纹匹配阈值 | 0.68 | 0.60–0.98 | 余弦相似度达到此值才匹配已有说话人，越低越宽松 |
| 新发言人确认句数 | 2 | 1–4 | 注册新说话人前需积累的句数，越高误注册越少 |
| 说话人预热时长 | 20 秒 | — | 累计有效语音超过此时长后才开始输出说话人标签，避免冷启动误判 |

点击字幕卡片上的说话人标签可将其改为其他说话人（手动修正）；点击侧边栏说话人标签可重命名，所有卡片立即同步更新。

---

## 录音

- 录音期间每 5 分钟写入一段，以限制内存占用。
- **暂停**临时停止麦克风流，保持当前会话开放；**继续**在同一 MP3 中追加录音。
- **停止**后，各段在后台合并为单个文件：`ASRLive_YYYYMMDD_HHMMSS.mp3`，默认保存至 `~/Downloads`，窗口模式下弹出原生保存面板。
- 文件就绪后**回放栏**自动出现，支持播放/暂停、进度拖拽、音量控制和「跟随回放」模式（转录随音频同步滚动）。
- 每条转录记录保存其在 MP3 中的精确起始偏移量；在跟随模式下点击条目，播放器跳转到对应句子。
- 退出时应用最多等待 10 秒等待正在进行的编码完成后再退出。

---

## 导出

点击顶栏**导出 ▾**，选择语言筛选和格式，通过原生面板保存。

发言人标签（自定义名称或默认「发言人N」）包含在所有导出格式中。

| 格式 | 说明 |
|---|---|
| TXT | 每条带时间戳的纯文本 |
| SRT | 标准字幕格式，可导入视频编辑软件 |
| JSON | 完整信息：时间戳、延迟、ASR 原文、矫正文本、所有译文 |
| Markdown | 适合粘贴到 Obsidian、Notion 或其他 Markdown 工具 |

**语言筛选选项：** 全部语言混合 · 仅矫正原文 · 仅 ASR 原始文本 · 任意单一语言（包含该语言作为原文或译文的条目）。

对于长会话，超过最近 200 条的历史记录会流式写入 `~/Downloads` 的 JSONL 文件，导出时自动包含。

---

## 常见问题

**启动后窗口长时间显示「连接中」。**  
首次加载需 30–60 秒将模型读入统一内存，等待状态栏显示「就绪」后再操作。

**启动时提示 `Address already in use`。**  
`main.py` 启动时自动终止占用 17433 端口的进程。如问题持续：`lsof -ti :17433 | xargs kill -9`

**麦克风权限错误：`PortAudioError -9986`。**  
系统设置 → 隐私与安全性 → 麦克风 → 为终端或 Lancer1911 ASR Live.app 授权。

**出现「nope nope nope…」等重复幻觉。**  
应用自动过滤大多数情况。如仍出现，将 VAD 灵敏度提高至 0.6–0.7，并确保句尾静音阈值至少为 0.5 s。

**没有翻译输出。**  
确认设置中已勾选翻译目标语言并保存。如仍无效，删除 `~/.asrlive_settings.json` 后重启。

**LLM 延迟过高（> 3 秒）。**  
切换为更小的模型（Qwen3-8B-4bit），或减少翻译目标语言数量。每增加一种目标语言，LLM 需多生成一个 JSON 字段。

**录音未保存。**  
确认已安装 ffmpeg（`brew install ffmpeg`）且设置中录音保存开关已开启。

**`No module named 'onnxruntime'`。**  
`pip install onnxruntime`

**使用 SenseVoice 时提示 `No module named 'mlx_audio'`。**  
`pip install mlx-audio`

**发言人识别显示「未安装」。**  
参照快速开始第 5 节安装。若依赖包已安装但模型缺失，运行 `hf download pyannote/embedding && hf download pyannote/segmentation-3.0`。也可点击**设置 → 检查发言人识别功能**查看应用内引导。

**通过 ModelScope 下载的模型未被识别。**  
确认模型权重（`.safetensors` / `.bin` / `.npz`）存放于 `~/.cache/huggingface/hub/models--<org>--<name>/` 目录根层。应用每次启动时自动扫描，无需手动配置路径。

**模型下载缓慢或失败。**  
使用 HuggingFace 镜像：`export HF_ENDPOINT=https://hf-mirror.com`

---

## 依赖项

| 项目 | 用途 |
|---|---|
| [mlx-whisper](https://github.com/ml-explore/mlx-examples) | 通过 MLX 在 Apple Silicon 上运行 Whisper |
| [mlx-lm](https://github.com/ml-explore/mlx-examples) | 通过 MLX 在 Apple Silicon 上运行 LLM |
| [mlx-audio](https://github.com/Blaizzy/mlx-audio) | 通过 MLX 在 Apple Silicon 上运行 SenseVoice |
| [Silero VAD](https://github.com/snakers4/silero-vad) | 语音活动检测 |
| [FastAPI](https://fastapi.tiangolo.com) | 后端 API 与 WebSocket 服务器 |
| [pywebview](https://pywebview.flowrl.com) | 原生 macOS 窗口（WKWebView） |
| [sounddevice](https://python-sounddevice.readthedocs.io) | 通过 PortAudio 采集麦克风 |
| [ffmpeg](https://ffmpeg.org) | 录音 MP3 编码 |
| [Qwen3](https://huggingface.co/Qwen) | 语义矫正与翻译 LLM |
| [Whisper large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo) | 默认 ASR 模型 |
| [SenseVoiceSmall](https://huggingface.co/FunAudioLLM/SenseVoiceSmall) | 可选高速 ASR 模型 |
| [pyannote-audio](https://github.com/pyannote/pyannote-audio) | 说话人识别声纹 embedding |
| [PyTorch](https://pytorch.org) | pyannote-audio 所需依赖 |

---

## 许可证

MIT
