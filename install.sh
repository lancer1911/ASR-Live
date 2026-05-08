#!/bin/bash
# macOS 内置 bash 是 3.2（不支持 ${var,,} 等 bash4 语法）
# 本脚本已改写为兼容 bash 3.2 的写法，无需 Homebrew bash
# =============================================================================
#  Lancer1911 ASR Live — 安装脚本
#  Install Script
#
#  用法 / Usage:
#    bash install.sh          # 标准安装（仅必需依赖）
#    bash install.sh --full   # 完整安装（含说话人识别 pyannote）
#    bash install.sh --sensevoice  # 含 SenseVoice 引擎
#    bash install.sh --full --sensevoice  # 全部
# =============================================================================

set -euo pipefail

# ── 颜色输出 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; }
section() { echo -e "\n${BOLD}━━━  $*  ━━━${NC}"; }

# ── 参数解析 ─────────────────────────────────────────────────────────────────
INSTALL_FULL=false
INSTALL_SV=false
for arg in "$@"; do
    [[ "$arg" == "--full" ]]        && INSTALL_FULL=true
    [[ "$arg" == "--sensevoice" ]]  && INSTALL_SV=true
done

# ── 脚本所在目录（即 app 根目录） ────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$HOME/asr-env"
PYTHON_MIN="3.11"

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   Lancer1911 ASR Live — Installer        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
[[ "$INSTALL_FULL" == true ]] && info "Mode: full (including speaker diarization)"
[[ "$INSTALL_SV"   == true ]] && info "Mode: SenseVoice engine included"
echo ""

# ═══════════════════════════════════════════════════════════════════════════════
section "1 / 6  Hardware & OS Check"
# ═══════════════════════════════════════════════════════════════════════════════

# Apple Silicon
ARCH=$(uname -m)
if [[ "$ARCH" != "arm64" ]]; then
    error "This app requires Apple Silicon (M-series chip). Detected: $ARCH"
    exit 1
fi
ok "Apple Silicon detected"

# macOS version ≥ 13
OS_VER=$(sw_vers -productVersion)
OS_MAJOR=$(echo "$OS_VER" | cut -d. -f1)
if [[ "$OS_MAJOR" -lt 13 ]]; then
    error "macOS 13 Ventura or later required. Current: $OS_VER"
    exit 1
fi
ok "macOS $OS_VER"

# RAM ≥ 24 GB
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if [[ "$RAM_GB" -lt 24 ]]; then
    warn "Only ${RAM_GB} GB RAM detected. 24 GB minimum recommended."
    warn "The app may crash or run very slowly on this machine."
    read -r -p "Continue anyway? [y/N] " ans
    ans_lower=$(echo "$ans" | tr "[:upper:]" "[:lower:]")
    [[ "$ans_lower" == "y" ]] || exit 1
else
    ok "${RAM_GB} GB unified memory"
fi

# Disk space ≥ 15 GB free
FREE_GB=$(( $(df -k "$HOME" | tail -1 | awk '{print $4}') / 1024 / 1024 ))
if [[ "$FREE_GB" -lt 15 ]]; then
    error "Less than 15 GB free disk space (${FREE_GB} GB). Models require ~11 GB."
    exit 1
fi
ok "${FREE_GB} GB free disk space"

# ═══════════════════════════════════════════════════════════════════════════════
section "2 / 6  Homebrew & System Dependencies"
# ═══════════════════════════════════════════════════════════════════════════════

# Homebrew
if ! command -v brew &>/dev/null; then
    info "Homebrew not found — installing..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    # Add brew to PATH for this session (Apple Silicon default path)
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
fi
ok "Homebrew $(brew --version | head -1)"

# ffmpeg (required for MP3 encoding)
if ! command -v ffmpeg &>/dev/null; then
    info "Installing ffmpeg..."
    brew install ffmpeg
fi
ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"

# ═══════════════════════════════════════════════════════════════════════════════
section "3 / 6  Python Environment"
# ═══════════════════════════════════════════════════════════════════════════════

# Find Python 3.11+
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" &>/dev/null; then
        VER=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
            PYTHON_BIN="$candidate"
            ok "Found Python $VER at $(command -v $candidate)"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    info "Python $PYTHON_MIN+ not found — installing via Homebrew..."
    brew install python@3.11
    PYTHON_BIN="python3.11"
fi

# Create virtual environment
if [[ -d "$VENV_DIR" ]]; then
    warn "Virtual environment already exists at $VENV_DIR"
    read -r -p "Re-use existing environment? [Y/n] " ans
    if [[ "$(echo "$ans" | tr "[:upper:]" "[:lower:]")" == "n" ]]; then
        info "Removing old environment..."
        rm -rf "$VENV_DIR"
        "$PYTHON_BIN" -m venv "$VENV_DIR"
        ok "New virtual environment created at $VENV_DIR"
    else
        ok "Using existing environment"
    fi
else
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR"
fi

# Activate
source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# ═══════════════════════════════════════════════════════════════════════════════
section "4 / 6  Python Packages"
# ═══════════════════════════════════════════════════════════════════════════════

info "Installing core packages (this may take 3–5 minutes)..."
pip install --quiet \
    "fastapi>=0.111.0" \
    "uvicorn[standard]>=0.30.0" \
    "pywebview>=5.1" \
    "mlx-whisper>=0.4.0" \
    "mlx-lm>=0.16.0" \
    "silero-vad>=5.1.2" \
    "sounddevice>=0.4.7" \
    "numpy>=1.26.0" \
    "onnxruntime"
ok "Core packages installed"

if [[ "$INSTALL_SV" == true ]]; then
    info "Installing SenseVoice engine (mlx-audio)..."
    pip install --quiet "mlx-audio>=0.4.3"
    ok "mlx-audio installed"
fi

if [[ "$INSTALL_FULL" == true ]]; then
    info "Installing speaker diarization packages (torch + pyannote — ~2 GB download)..."
    pip install --quiet torch omegaconf "pyannote.audio>=3.3.0"
    ok "Speaker diarization packages installed"
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "5 / 6  Model Downloads"
# ═══════════════════════════════════════════════════════════════════════════════

# huggingface_hub CLI
if ! command -v hf &>/dev/null; then
    pip install --quiet huggingface_hub[cli]
fi

HF_CACHE="$HOME/.cache/huggingface/hub"

download_model() {
    local repo="$1"
    local label="$2"
    local size="$3"
    local dir_name
    dir_name="models--$(echo "$repo" | tr '/' '--')"
    if [[ -d "$HF_CACHE/$dir_name" ]]; then
        ok "$label already cached — skipping"
    else
        info "Downloading $label ($size)..."
        info "  Tip: if slow, set HF_ENDPOINT=https://hf-mirror.com"
        hf download "$repo" || {
            warn "Download failed for $repo. You can retry later from the app's model guide."
        }
    fi
}

# Required models
download_model "mlx-community/whisper-large-v3-turbo" "Whisper large-v3-turbo (ASR)" "~3 GB"
download_model "mlx-community/Qwen3-14B-4bit"         "Qwen3-14B-4bit (LLM)"        "~8 GB"

# Optional: SenseVoice
if [[ "$INSTALL_SV" == true ]]; then
    download_model "mlx-community/SenseVoiceSmall" "SenseVoiceSmall (alternative ASR)" "~0.5 GB"
fi

# Optional: speaker diarization
if [[ "$INSTALL_FULL" == true ]]; then
    info "Speaker diarization models require a HuggingFace account and model agreement."
    info "Steps:"
    info "  1. Visit https://huggingface.co/settings/tokens and create a Read token"
    info "  2. Visit https://huggingface.co/pyannote/embedding and agree to terms"
    info "  3. Visit https://huggingface.co/pyannote/segmentation-3.0 and agree to terms"
    read -r -p "Have you completed the above steps? [y/N] " ans
    if [[ "$(echo "$ans" | tr "[:upper:]" "[:lower:]")" == "y" ]]; then
        hf auth login
        download_model "pyannote/embedding"        "pyannote embedding"        "~0.3 GB"
        download_model "pyannote/segmentation-3.0" "pyannote segmentation-3.0" "~0.2 GB"
    else
        warn "Skipping speaker diarization models. You can install them later via Settings → Check Speaker ID Setup."
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════════
section "6 / 6  Launcher"
# ═══════════════════════════════════════════════════════════════════════════════

LAUNCHER="$HOME/Desktop/ASR Live.command"
cat > "$LAUNCHER" << LAUNCH
#!/bin/bash
source "$VENV_DIR/bin/activate"
cd "$SCRIPT_DIR"
python main.py
LAUNCH
chmod +x "$LAUNCHER"
ok "Launcher created: ~/Desktop/ASR Live.command"

# ── 完成 ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Installation complete ✓                ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Start the app:   ${BOLD}double-click  ~/Desktop/ASR Live.command${NC}"
echo -e "  Or from terminal: ${BOLD}source $VENV_DIR/bin/activate && python $SCRIPT_DIR/main.py${NC}"
echo ""
if [[ "$INSTALL_FULL" == false ]]; then
    echo -e "  Speaker diarization not installed. Run with ${BOLD}--full${NC} to add it."
fi
if [[ "$INSTALL_SV" == false ]]; then
    echo -e "  SenseVoice not installed. Run with ${BOLD}--sensevoice${NC} to add it."
fi
echo ""
