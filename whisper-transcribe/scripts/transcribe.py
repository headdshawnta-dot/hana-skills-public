#!/usr/bin/env python3
"""Transcribe audio files using mlx-whisper large-v3 on Apple Silicon.

Usage:
    python3.14 transcribe.py audio.m4a
    python3.14 transcribe.py audio.mp3 --language zh

Output (JSON):
{
    "file": "audio.m4a",
    "language": "zh",
    "duration_seconds": 123.4,
    "segments": [
        {"start": 0.0, "end": 5.2, "text": "今天开会主要讨论三个问题"},
        ...
    ]
}
"""

import argparse
import json
import os
import sys
from pathlib import Path


# Fixed model — only large-v3 is used
MODEL_REPO = "mlx-community/whisper-large-v3-mlx"
MODELSCOPE_CACHE = os.path.expanduser("~/.cache/huggingface/hub/models")


def resolve_model_path() -> str:
    """Find large-v3 model in local cache, or return HF repo id for download."""
    org, repo = MODEL_REPO.split("/", 1)
    cache_dir = os.path.join(MODELSCOPE_CACHE, f"{org}--{repo}")
    if os.path.isdir(cache_dir):
        snapshots = os.path.join(cache_dir, "snapshots")
        if os.path.isdir(snapshots):
            for snap in sorted(os.listdir(snapshots)):
                snap_path = os.path.join(snapshots, snap)
                if os.path.isfile(os.path.join(snap_path, "weights.npz")):
                    return snap_path
    return MODEL_REPO


def transcribe(file_path: str, language: str | None = None) -> dict:
    """Transcribe an audio file and return structured result."""
    import mlx_whisper

    model_path = resolve_model_path()
    result = mlx_whisper.transcribe(
        file_path,
        path_or_hf_repo=model_path,
        language=language,
    )

    return {
        "file": os.path.basename(file_path),
        "language": result.get("language", "unknown"),
        "duration_seconds": result.get("duration", 0),
        "segments": [
            {
                "start": round(seg.get("start", 0), 2),
                "end": round(seg.get("end", 0), 2),
                "text": seg.get("text", "").strip(),
            }
            for seg in result.get("segments", [])
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio files using mlx-whisper large-v3 (Apple Silicon)"
    )
    parser.add_argument("file", help="Path to audio file (m4a, mp3, wav, flac)")
    parser.add_argument(
        "--language", default=None,
        help="Language code (e.g. zh, en). Auto-detect if not specified."
    )
    parser.add_argument(
        "--output", default="json",
        choices=["json", "text"],
        help="Output format (default: json)"
    )
    parser.add_argument(
        "--full-text", action="store_true",
        help="Output only the full transcribed text, no metadata"
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"ERROR: File not found: {args.file}", sys.stderr)
        sys.exit(1)

    supported = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".aac", ".wma"}
    if file_path.suffix.lower() not in supported:
        print(
            f"WARNING: Unrecognized format '{file_path.suffix}'. "
            f"Supported: {', '.join(sorted(supported))}",
            file=sys.stderr,
        )

    result = transcribe(str(file_path), language=args.language)

    if args.full_text:
        for seg in result["segments"]:
            print(seg["text"])
    elif args.output == "text":
        for seg in result["segments"]:
            print(f"[{_fmt(seg['start'])} → {_fmt(seg['end'])}] {seg['text']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


if __name__ == "__main__":
    main()
