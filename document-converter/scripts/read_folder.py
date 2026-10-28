#!/usr/bin/env python3
"""Read a folder of documents.

Routing (--backend auto, the default):
  - MINERU_EXTS      -> MinerU cloud, submitted in batches (shared with try_mineru)
  - everything else -> local chain via read_document.py

Cloud failures fall back to the local chain (--backend local, no mineru).
Pre-check failures (unsupported extension / >200MB) hard-fail without retry.

Output mirrors each input's relative path under --output-dir:
  OUT/<rel>.md                    result
  OUT/<rel parent>/<stem>_mineru/ media (images/)
  OUT/<rel>.md.conversion.log     written by read_document.py when a local chain had failures
  OUT/_summary.json / _summary.md

Usage:
  python3 scripts/read_folder.py INPUT_DIR --output-dir OUT [--backend auto]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from office_common import MINERU_EXTS, mineru_extract, mineru_token

SCRIPT_DIR = Path(__file__).resolve().parent
READ_DOCUMENT = SCRIPT_DIR / "read_document.py"
LOCAL_BACKENDS = {"local", "docling", "vision", "markitdown", "direct"}
CLOUD_BACKENDS = {"auto", "mineru"}


def discover_files(root: Path) -> list[Path]:
    """Files under root, skipping hidden paths.

    Hidden components (any part starting with '.') are skipped so that backend
    scratch dirs left inside the input folder (.docling-out-*, .doc-convert-*)
    are not picked up as inputs on a re-run.
    """
    found: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        found.append(p)
    return sorted(found)


def plan_outputs(out_dir: Path, root: Path, files: list[Path]) -> dict[Path, Path]:
    """Map each source to its output .md path, disambiguating stem collisions.

    Normally OUT/<rel>.md. When two sources in the same folder share a stem
    (e.g. a.pdf and a.docx), both become OUT/<name>.md (keeping the original
    extension) so neither silently overwrites the other.
    """
    counts: dict[tuple[Path, str], int] = {}
    for f in files:
        rel = f.relative_to(root)
        counts[(rel.parent, rel.stem)] = counts.get((rel.parent, rel.stem), 0) + 1
    mapping: dict[Path, Path] = {}
    for f in files:
        rel = f.relative_to(root)
        if counts[(rel.parent, rel.stem)] > 1:
            md = out_dir / rel.parent / f"{f.name}.md"
        else:
            md = out_dir / rel.parent / f"{rel.stem}.md"
        md.parent.mkdir(parents=True, exist_ok=True)
        mapping[f] = md
    return mapping


def _actual_reader(md: Path, backend: str) -> str | None:
    """Which backend actually produced md.

    The requested backend is not the answer: 'auto' can end on markitdown and
    'local' starts at docling, so a summary claiming reader=auto told you nothing.
    With a .conversion.log the last successful line names the winner; with no log
    the first tier succeeded cleanly.
    """
    log = md.with_suffix(md.suffix + ".conversion.log")
    if log.exists():
        winner = None
        for line in log.read_text("utf-8", "replace").splitlines():
            if line.startswith("backend=") and "status=ok" in line:
                winner = line.split(",", 1)[0].replace("backend=", "").strip()
        if winner:
            return winner
    first_tier = {
        "local": "docling", "auto": "mineru", "mineru": "mineru",
        "docling": "docling", "vision": "vision",
        "markitdown": "markitdown", "direct": "direct",
    }
    return first_tier.get(backend, backend)


def run_local(src: Path, md: Path, backend: str, args: argparse.Namespace) -> dict:
    """Convert one file through the local chain (via read_document.py subprocess)."""
    cmd = [
        sys.executable, str(READ_DOCUMENT), str(src),
        "--output", str(md), "--format", "markdown",
        "--backend", backend,
        "--docling-timeout", str(args.docling_timeout),
        "--max-chars", str(args.max_chars),
        "--max-rows", str(args.max_rows),
        "--max-cols", str(args.max_cols),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = proc.returncode == 0 and md.exists() and md.stat().st_size > 0
    return {
        "status": "ok" if ok else "fail",
        "reader": _actual_reader(md, backend) if ok else None,
        "error": None if ok else (proc.stderr or proc.stdout or "read_document.py failed").strip()[-300:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Read a folder of documents (MinerU cloud first, local chain as fallback).")
    ap.add_argument("input", help="Input folder.")
    ap.add_argument("--output-dir", required=True, help="Output folder for .md files, media, logs, and _summary.")
    ap.add_argument("--backend", choices=["auto", "mineru", "local", "docling", "vision", "markitdown", "direct"], default="auto")
    ap.add_argument("--docling-timeout", type=int, default=600)
    ap.add_argument("--max-chars", type=int, default=200000)
    ap.add_argument("--max-rows", type=int, default=80)
    ap.add_argument("--max-cols", type=int, default=30)
    args = ap.parse_args()

    root = Path(args.input).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({"ok": False, "error": f"Not a folder: {root}"}, ensure_ascii=False, indent=2))
        return 1
    out_dir = Path(args.output_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = discover_files(root)
    outputs = plan_outputs(out_dir, root, files)
    records: list[dict] = []

    use_cloud = args.backend in CLOUD_BACKENDS
    token = mineru_token() if use_cloud else None

    if args.backend in LOCAL_BACKENDS:
        local_backend = args.backend
    elif use_cloud and not token:
        local_backend = "local"  # no token: whole mineru group falls back to the local chain
    else:
        local_backend = "auto"

    cloud_files: list[Path] = []
    local_files: list[Path] = []
    if args.backend == "mineru" and token:
        cloud_files = list(files)
    elif args.backend == "auto" and token:
        cloud_files = [f for f in files if f.suffix.lower() in MINERU_EXTS]
        cloud_set = set(cloud_files)
        local_files = [f for f in files if f not in cloud_set]
    else:
        local_files = list(files)

    # --- cloud group: batch per parent dir so each dir has one media_root ---
    by_dir: dict[Path, list[Path]] = {}
    for f in cloud_files:
        by_dir.setdefault(f.parent, []).append(f)

    for parent, group in sorted(by_dir.items()):
        media_root = out_dir / parent.relative_to(root)
        media_root.mkdir(parents=True, exist_ok=True)
        for v in mineru_extract(group, token=token, media_root=media_root):
            src = Path(v["file"])
            md = outputs[src]
            if v["status"] == "ok" and v.get("text"):
                md.write_text(v["text"], "utf-8")
                records.append({"input": str(src), "output": str(md), "reader": "mineru",
                                "status": "ok", "elapsed": v.get("elapsed"), "error": None})
                continue
            if v.get("precheck"):
                # MinerU refused on a precheck rule (unsupported type / oversize).
                # Still try the local chain: docling reads a 201MB PDF that MinerU
                # rejects, so hard-failing here used to drop a convertible file.
                # The MinerU reason is kept so the summary shows why it never
                # came from the cloud.
                res = run_local(src, md, "local", args)
                records.append({"input": str(src), "output": str(md) if res["status"] == "ok" else None,
                                "reader": res.get("reader"), "status": res["status"],
                                "elapsed": v.get("elapsed"),
                                "mineru_status": v.get("status"),
                                "mineru_reason": v.get("reason"),
                                "error": None if res["status"] == "ok" else (res.get("error") or v.get("reason"))})
                continue
            # cloud failure: fall back to the local chain (no mineru)
            res = run_local(src, md, "local", args)
            records.append({"input": str(src), "output": str(md) if res["status"] == "ok" else None,
                            "reader": res.get("reader"), "status": res["status"],
                            "elapsed": v.get("elapsed"),
                            "mineru_status": v.get("status"),
                            "mineru_reason": v.get("reason"),
                            "error": None if res["status"] == "ok" else (v.get("reason") or res.get("error"))})

    # --- local group ---
    for f in local_files:
        md = outputs[f]
        res = run_local(f, md, local_backend, args)
        records.append({"input": str(f), "output": str(md) if res["status"] == "ok" else None,
                        "reader": res.get("reader"), "status": res["status"],
                        "elapsed": None, "error": res.get("error")})

    ok_count = sum(1 for r in records if r["status"] == "ok")
    summary = {
        "ok": ok_count == len(records),
        "input": str(root),
        "output_dir": str(out_dir),
        "backend": args.backend,
        "total": len(records),
        "ok_count": ok_count,
        "fail_count": len(records) - ok_count,
        "results": records,
    }
    (out_dir / "_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), "utf-8")

    lines = [
        "# read_folder summary", "",
        f"- input: `{root}`",
        f"- backend: `{args.backend}`",
        f"- total: {len(records)}  ok: {ok_count}  fail: {len(records) - ok_count}", "",
        "| input | reader | status | output |", "|---|---|---|---|",
    ]
    for r in records:
        rel_in = Path(r["input"]).relative_to(root)
        out = Path(r["output"]).relative_to(out_dir) if r["output"] else "-"
        lines.append(f"| {rel_in} | {r['reader'] or '-'} | {r['status']} | {out} |")
    (out_dir / "_summary.md").write_text("\n".join(lines) + "\n", "utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
