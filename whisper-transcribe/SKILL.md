---
name: whisper-transcribe
description: >
  Transcribe audio files to text using local mlx-whisper on Apple Silicon, then optionally produce meeting minutes. Use when the user provides an audio file (m4a/mp3/wav/flac) and says "transcribe", "转文字", "录音转文本", "转录", "帮我听一下", "语音转文字". Also use for meeting recording workflows: transcribe first, then use LLM to filter filler words, infer speaker roles, extract decisions and action items. Do NOT use for speaker diarization (pyannote is intentionally not installed) or real-time microphone streaming.
compatibility: "Requires Python 3.14 at /opt/homebrew/bin/python3.14 with mlx-whisper 0.4.3. HF_ENDPOINT set to https://hf-mirror.com for model download in China. Apple Silicon only (MLX framework)."
---

# Whisper Transcribe

Local speech-to-text on Apple Silicon using mlx-whisper. No API keys, no cloud, no speaker diarization — just accurate transcription plus LLM-powered summarization.

## When to trigger

User provides an audio file path and asks for:
- Transcription / 转录 / 转文字
- "What's in this recording?" / "这段录音说了什么"
- Meeting minutes from audio / 把录音做成会议纪要
- "Listen to this and summarize" / 帮我听一下总结

Do NOT trigger for:
- Real-time microphone streaming (not supported)
- Speaker diarization / 说话人分离 (intentionally out of scope)
- Audio files without any intent to transcribe (user just attaching a file doesn't trigger)

## Workflow

### Phase 1: Transcribe

Run the bundled script. Always use Python 3.14 with HF mirror and xet disabled:

```bash
HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1 \
  /opt/homebrew/bin/python3.14 scripts/transcribe.py <audio_file> --language zh
```

Why `HF_HUB_DISABLE_XET=1`: huggingface_hub >=1.22 defaults to xet protocol for large files, which bypasses the mirror and hits `cas-server.xethub.hf.co` directly (401 in China). Disabling xet forces plain HTTP download through the mirror.

Parameters:
- `--language`: `zh` for Chinese (default for Chinese-speaking user), omit for auto-detect.
- `--output json`: structured output with segments and timestamps (default).

Model is fixed at large-v3 (pre-downloaded via modelscope). No `--model` flag needed.

The script outputs JSON to stdout:
```json
{
  "file": "meeting.m4a",
  "language": "zh",
  "duration_seconds": 3600.0,
  "model": "mlx-community/whisper-large-v3-mlx",
  "segments": [
    {"start": 0.0, "end": 5.2, "text": "今天开会主要讨论三个问题"},
    {"start": 5.3, "end": 12.1, "text": "第一个是预算调整方案"}
  ]
}
```

**First-time model download:** If the model is not in local cache, use modelscope to download (hf-mirror large-file CDN is unreliable in China):

```bash
/opt/homebrew/bin/python3.14 -c "
import os
from modelscope import snapshot_download
snapshot_download('mlx-community/whisper-large-v3-mlx',
                  cache_dir=os.path.expanduser('~/.cache/huggingface/hub/'))
"
```

The script auto-detects modelscope-cached models at `~/.cache/huggingface/hub/models/` and uses them without any network access. HF download through mirror is the fallback.

**Error handling:**
- File not found: the script exits with a clear error message
- Unsupported format: warns but attempts transcription anyway (mlx-whisper handles format detection)
- Model download failure via HF: use modelscope to download instead
- Empty transcript: the audio may be silent or language mismatch; try `--language zh` explicitly
- MLX hangs on first inference: this is Metal shader compilation (~30s one-time, subsequent runs are instant)

### Phase 2: Summarize (only if user wants meeting minutes)

When the user asks for meeting minutes / 会议纪要, feed the transcribed text to LLM reasoning. Do NOT just dump the raw transcript.

Read the JSON output from Phase 1. Concatenate segments into a continuous text block, then use this prompt pattern:

```
The following is a transcript of a meeting. Your task:

1. Filter out filler words and false starts (um, ah, 那个, 这个, 就是, 然后...然后, repetitions, verbal tics). Remove them from the output entirely — do not flag them, just exclude them.
2. Infer speaker roles from content — who is reporting, who is deciding, who is asking questions. Use context labels like "汇报方" / "决策方" / "提问方" rather than generic SPEAKER_A.
3. Produce structured meeting minutes in this format:

## 会议概要
[2-3 sentences]

## 关键决策
- [Decision 1]
- [Decision 2]

## 行动项
- [ ] [Action] — 负责人: [name/role if identifiable], 截止: [deadline if mentioned]

## 讨论要点
### [Topic 1]
- [Key point]
- [Key point]

### [Topic 2]
- [Key point]

Only include information explicitly stated in the transcript. If a speaker's name or a deadline is not mentioned, use the role label or omit the field. Do not fabricate.
```

Apply this reasoning directly — do not spawn a subagent for it. You are the LLM; do the summarization yourself after reading the transcript.

## Model

Always use large-v3 (~3GB, pre-downloaded). M4 runs it at ~5x real-time. No other model sizes are needed — accuracy over speed for meeting transcription.

## Prerequisites

Already installed in the user's environment:
- Python 3.14 at `/opt/homebrew/bin/python3.14`
- mlx-whisper 0.4.3
- `HF_ENDPOINT=https://hf-mirror.com` (set in environment)

No additional setup needed before use.
