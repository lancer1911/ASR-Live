/**
 * ASR Live – UI language strings
 * To add a new language: copy the `en` block, rename the key, translate every value.
 * Keys must stay identical across all languages.
 */
const I18N = {

  zh: {
    /* ── meta ── */
    langBtnLabel:       'EN',          // label shown on the toggle button (switch TO this)
    htmlLang:           'zh',

    /* ── topbar ── */
    btnStart:           '● 开始',
    btnPause:           '⏸ 暂停',
    btnResume:          '▶ 继续',
    btnStop:            '■ 停止',
    btnExport:          '导出 ▾',
    btnClear:           '清空',
    btnSettings:        '设置',
    btnThemeTitle:      '切换主题',
    settingsDisabled:   '录音过程中无法修改设置，请先停止录音',

    /* ── status bar ── */
    statusConnecting:   '连接中…',
    statusReady:        '就绪',
    statusRecording:    '识别中 · {whisper} · {llm}',
    statusPaused:       '已暂停（点继续）',
    statusReconnecting: '重连中…',
    statusConnected:    '已连接',

    /* ── sidebar: recording ── */
    sideRecControl:     '录音控制',
    recStart:           '开始识别',
    recRunning:         '识别中…',
    recPaused:          '已暂停',
    sidePause:          '⏸ 暂停',
    sideResume:         '▶ 继续',
    sideStop:           '■ 停止',

    /* ── sidebar: language ── */
    sideLangLabel:      '识别语言',
    langZH:             '中文',
    langEN:             '英文',
    langJA:             '日文',

    /* ── sidebar: quick adjust ── */
    sideQuickAdj:       '快速调节',
    sliderSilence:      '句尾静音',
    sliderVAD:          'VAD 灵敏度',

    /* ── sidebar: metrics ── */
    sideMetrics:        '实时指标',
    metASR:             'ASR 时滞',
    metLLM:             'LLM 时滞',
    metTotal:           '合计',
    metSent:            '句数',
    metChar:            '字数',
    metRuntime:         '运行时长',

    /* ── waveform / timer ── */
    // (timer is numeric, no translation needed)

    /* ── subtitle area ── */
    emptyHint:          '点击「开始识别」<br>开始实时语音转写',
    liveProcessing:     'LLM 处理中…',

    /* ── player bar ── */
    merging:            '正在合并录音…',
    mergingSegment:     '合并中… 已完成分段 {n}',
    followLabel:        '跟随',
    volTitle:           '音量',

    /* ── search bar ── */
    searchPlaceholder:  '搜索字幕内容…',
    searchClearTitle:   '清除搜索',
    btnLatest:          '↓ 最新',

    /* ── settings panel ── */
    settingsTitle:      '设置',
    btnSave:            '保存设置',
    btnCancel:          '取消',

    sectASR:            'ASR 模型',
    labelWhisper:       'Whisper 模型',
    whisperHint:        '大=准但慢',
    whisperCurrent:     '{name}（当前）',
    whisperNone:        '未找到本地 Whisper 模型',
    labelLLM:           'LLM 矫正模型',
    llmHint:            '影响翻译质量',
    llmLoading:         '加载中…',
    llmCurrent:         '{name}（当前）',
    llmNone:            '未找到本地 LLM 模型',

    sectScene:          '场景与术语',
    labelContext:       '场景描述 / 专业术语',
    contextHint:        '留空则不启用',
    contextPlaceholder: '例如：这是一段医疗会诊对话，涉及以下术语：心房颤动、左心室射血分数、冠状动脉旁路移植术\n\n或：This is a tech product review. Key terms: Apple Silicon, Neural Engine, MLX framework, unified memory',
    contextDesc:        '同时作为 Whisper 的词汇提示和 LLM 矫正的参考，对专业名词识别有显著提升',

    sectTranslate:      '翻译目标',
    labelOutputLangs:   '输出语言',
    outputLangsHint:    '可多选，未选则仅矫正',
    transDesc:          '中文输入→翻译至选定语言；英/日文输入→同理双向翻译',
    togZH:              '中文',
    togEN:              '英文',
    togJA:              '日文',
    togKO:              '韩文',
    togFR:              '法文',
    togDE:              '德文',
    togES:              '西班牙文',

    sectVAD:            'VAD · 音频',
    labelSilence:       '句尾静音阈值',
    labelVADSens:       'VAD 灵敏度',
    labelMaxSent:       '最长单句时长',
    labelCtxSent:       '上下文句数',
    ctxUnit:            '句',           // unit appended to slider value, e.g. "6句"

    sectAudio:          '音频输入',
    labelRecSave:       '录音保存',
    recSaveHint:        '停止时自动保存 MP3',
    labelBitrate:       'MP3 码率',
    bitrateHint:        '越高越清晰',
    bitrate64:          '64 kbps（省空间，约 0.5MB/分钟）',
    bitrate128:         '128 kbps（标准质量，约 1MB/分钟）',
    bitrate192:         '192 kbps（高质量，约 1.5MB/分钟）',
    bitrate320:         '320 kbps（无损级，约 2.5MB/分钟）',
    labelMic:           '麦克风设备',
    micDefault:         '系统默认麦克风',
    micDetecting:       '检测中…',
    micFailed:          '获取设备失败',
    micReloadTitle:     '重新检测麦克风',

    sectUI:             '界面',
    labelFontSize:      '字幕字号',
    fontSmall:          '小（13px）',
    fontMedium:         '中（15px）',
    fontLarge:          '大（17px）',
    fontXL:             '特大（20px）',
    labelAutoScroll:    '自动滚动',
    autoScrollOn:       '开启',
    autoScrollOff:      '关闭',

    /* ── export dialog ── */
    exportTitle:        '导出字幕',
    exportLangLabel:    '导出语言',
    exportAll:          '全部语言（混合）',
    exportCorrected:    '仅原文（矫正后）',
    exportRaw:          '仅原文（ASR 原始）',
    exportOnlyLang:     '仅{lang}',
    exportFormatLabel:  '文件格式',
    exportTxt:          '纯文本 TXT',
    exportSrt:          'SRT 字幕文件',
    exportJson:         'JSON（含时间戳）',
    exportMd:           'Markdown',

    /* ── toasts / alerts ── */
    clearConfirm:       '清空所有字幕和上下文记录？',
    toastSaved:         '已保存：{name}',
    toastRecSaved:      '录音已保存：{name}{size}',
    scanFailed:         '扫描失败',

    /* ── download guide ── */
    dlTitle:            '欢迎使用 ASR Live',
    dlSub:              '首次使用需要下载 AI 模型（约 11 GB），下载完成后完全离线运行。<br>请确保网络连接正常，下载过程中请勿关闭窗口。',
    dlCached:           '✓ 已下载',
    dlWaiting:          '等待下载…',
    dlConnecting:       '连接中…',
    dlDownloading:      '下载中…',
    dlDone:             '✓ 下载完成',
    dlFailed:           '✗ 下载失败，请检查网络后重试',
    dlError:            '✗ {msg}',
    dlSkip:             '稍后下载，先体验界面',
    dlStart:            '开始下载',
    dlStarting:         '下载中…',
    dlAllDone:          '全部完成，进入应用',

    /* ── language display names (used in subtitle badges & translation rows) ── */
    langName: { zh:'中文', en:'英文', ja:'日文', ko:'韩文', fr:'法文', de:'德文', es:'西班牙文' },
  },

  en: {
    /* ── meta ── */
    langBtnLabel:       '中文',
    htmlLang:           'en',

    /* ── topbar ── */
    btnStart:           '● Start',
    btnPause:           '⏸ Pause',
    btnResume:          '▶ Resume',
    btnStop:            '■ Stop',
    btnExport:          'Export ▾',
    btnClear:           'Clear',
    btnSettings:        'Settings',
    btnThemeTitle:      'Toggle theme',
    settingsDisabled:   'Settings unavailable while recording. Stop first.',

    /* ── status bar ── */
    statusConnecting:   'Connecting…',
    statusReady:        'Ready',
    statusRecording:    'Recording · {whisper} · {llm}',
    statusPaused:       'Paused (click Resume)',
    statusReconnecting: 'Reconnecting…',
    statusConnected:    'Connected',

    /* ── sidebar: recording ── */
    sideRecControl:     'Recording',
    recStart:           'Start ASR',
    recRunning:         'Recognizing…',
    recPaused:          'Paused',
    sidePause:          '⏸ Pause',
    sideResume:         '▶ Resume',
    sideStop:           '■ Stop',

    /* ── sidebar: language ── */
    sideLangLabel:      'Input Language',
    langZH:             'Chinese',
    langEN:             'English',
    langJA:             'Japanese',

    /* ── sidebar: quick adjust ── */
    sideQuickAdj:       'Quick Adjust',
    sliderSilence:      'End Silence',
    sliderVAD:          'VAD Sensitivity',

    /* ── sidebar: metrics ── */
    sideMetrics:        'Live Metrics',
    metASR:             'ASR Latency',
    metLLM:             'LLM Latency',
    metTotal:           'Total',
    metSent:            'Sentences',
    metChar:            'Characters',
    metRuntime:         'Runtime',

    /* ── subtitle area ── */
    emptyHint:          'Click "Start ASR"<br>to begin live transcription',
    liveProcessing:     'LLM processing…',

    /* ── player bar ── */
    merging:            'Merging audio…',
    mergingSegment:     'Merging… segment {n} done',
    followLabel:        'Follow',
    volTitle:           'Volume',

    /* ── search bar ── */
    searchPlaceholder:  'Search subtitles…',
    searchClearTitle:   'Clear search',
    btnLatest:          '↓ Latest',

    /* ── settings panel ── */
    settingsTitle:      'Settings',
    btnSave:            'Save',
    btnCancel:          'Cancel',

    sectASR:            'ASR Model',
    labelWhisper:       'Whisper Model',
    whisperHint:        'larger = more accurate but slower',
    whisperCurrent:     '{name} (current)',
    whisperNone:        'No local Whisper model found',
    labelLLM:           'LLM Correction Model',
    llmHint:            'affects translation quality',
    llmLoading:         'Loading…',
    llmCurrent:         '{name} (current)',
    llmNone:            'No local LLM model found',

    sectScene:          'Scene & Terminology',
    labelContext:       'Scene Description / Terminology',
    contextHint:        'leave blank to disable',
    contextPlaceholder: 'e.g. This is a medical consultation. Key terms: atrial fibrillation, LVEF, CABG\n\nor: Tech product review. Key terms: Apple Silicon, Neural Engine, MLX framework, unified memory',
    contextDesc:        'Used as vocabulary hint for Whisper and correction reference for LLM — significantly improves specialized term recognition.',

    sectTranslate:      'Translation Targets',
    labelOutputLangs:   'Output Languages',
    outputLangsHint:    'multi-select; if none selected, only correction is applied',
    transDesc:          'Chinese input → translated to selected languages; English/Japanese input → bidirectional.',
    togZH:              'Chinese',
    togEN:              'English',
    togJA:              'Japanese',
    togKO:              'Korean',
    togFR:              'French',
    togDE:              'German',
    togES:              'Spanish',

    sectVAD:            'VAD · Audio',
    labelSilence:       'End-of-sentence Silence',
    labelVADSens:       'VAD Sensitivity',
    labelMaxSent:       'Max Sentence Length',
    labelCtxSent:       'Context Sentences',
    ctxUnit:            '',             // English: plain number, no unit suffix

    sectAudio:          'Audio Input',
    labelRecSave:       'Save Recording',
    recSaveHint:        'auto-saves MP3 on stop',
    labelBitrate:       'MP3 Bitrate',
    bitrateHint:        'higher = better quality',
    bitrate64:          '64 kbps (compact, ~0.5 MB/min)',
    bitrate128:         '128 kbps (standard, ~1 MB/min)',
    bitrate192:         '192 kbps (high quality, ~1.5 MB/min)',
    bitrate320:         '320 kbps (lossless-grade, ~2.5 MB/min)',
    labelMic:           'Microphone',
    micDefault:         'System default microphone',
    micDetecting:       'Detecting…',
    micFailed:          'Failed to load devices',
    micReloadTitle:     'Re-detect microphone',

    sectUI:             'Interface',
    labelFontSize:      'Subtitle Font Size',
    fontSmall:          'Small (13px)',
    fontMedium:         'Medium (15px)',
    fontLarge:          'Large (17px)',
    fontXL:             'X-Large (20px)',
    labelAutoScroll:    'Auto Scroll',
    autoScrollOn:       'On',
    autoScrollOff:      'Off',

    /* ── export dialog ── */
    exportTitle:        'Export Subtitles',
    exportLangLabel:    'Language',
    exportAll:          'All languages (mixed)',
    exportCorrected:    'Source only (corrected)',
    exportRaw:          'Source only (raw ASR)',
    exportOnlyLang:     '{lang} only',
    exportFormatLabel:  'Format',
    exportTxt:          'Plain Text TXT',
    exportSrt:          'SRT Subtitle File',
    exportJson:         'JSON (with timestamps)',
    exportMd:           'Markdown',

    /* ── toasts / alerts ── */
    clearConfirm:       'Clear all subtitles and context history?',
    toastSaved:         'Saved: {name}',
    toastRecSaved:      'Recording saved: {name}{size}',
    scanFailed:         'Scan failed',

    /* ── download guide ── */
    dlTitle:            'Welcome to ASR Live',
    dlSub:              'First-time setup requires downloading AI models (~11 GB). After that, everything runs fully offline.<br>Ensure network access and do not close the window during download.',
    dlCached:           '✓ Already downloaded',
    dlWaiting:          'Waiting…',
    dlConnecting:       'Connecting…',
    dlDownloading:      'Downloading…',
    dlDone:             '✓ Download complete',
    dlFailed:           '✗ Download failed — check your network and retry',
    dlError:            '✗ {msg}',
    dlSkip:             'Skip for now, explore the UI',
    dlStart:            'Start Download',
    dlStarting:         'Downloading…',
    dlAllDone:          'All done — enter app',

    /* ── language display names ── */
    langName: { zh:'Chinese', en:'English', ja:'Japanese', ko:'Korean', fr:'French', de:'German', es:'Spanish' },
  },

};
