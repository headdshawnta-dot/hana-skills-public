#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

for _prefix, _uri in {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
}.items():
    ET.register_namespace(_prefix, _uri)


class DocumentSkillError(Exception):
    def __init__(self, message: str, *, code: str = "error", details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def json_success(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2)


def json_error(err: Exception) -> str:
    if isinstance(err, DocumentSkillError):
        payload = {"ok": False, "error": err.message, "code": err.code}
        if err.details:
            payload["details"] = err.details
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return json.dumps({"ok": False, "error": str(err), "code": "unexpected_error"}, ensure_ascii=False, indent=2)


def require_file(path: str | Path) -> Path:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise DocumentSkillError(f"File not found: {p}", code="file_not_found")
    if not p.is_file():
        raise DocumentSkillError(f"Path is not a file: {p}", code="not_a_file")
    return p


def prepare_output(input_path: Path, output_path: str | Path) -> Path:
    out = Path(output_path).expanduser().resolve()
    if out == input_path.resolve():
        raise DocumentSkillError("Output path must differ from input path. Save edits to a new file.", code="unsafe_overwrite")
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def load_ops(path: str | Path) -> list[dict[str, Any]]:
    p = require_file(path)
    try:
      data = json.loads(p.read_text("utf-8"))
    except json.JSONDecodeError as exc:
        raise DocumentSkillError(f"Invalid JSON operations file: {exc}", code="invalid_ops") from exc
    if isinstance(data, dict) and isinstance(data.get("operations"), list):
        data = data["operations"]
    if not isinstance(data, list):
        raise DocumentSkillError("Operations JSON must be a list or an object with an operations list.", code="invalid_ops")
    for i, op in enumerate(data):
        if not isinstance(op, dict) or not isinstance(op.get("op"), str):
            raise DocumentSkillError(f"Operation at index {i} must be an object with an op string.", code="invalid_ops")
    return data


def write_text_or_json(path: str | Path | None, content: str) -> None:
    if path:
        out = Path(path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, "utf-8")
    else:
        print(content)


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def attr_local(element: ET.Element, name: str, default: str | None = None) -> str | None:
    for key, value in element.attrib.items():
        if local_name(key) == name:
            return value
    return default


def element_text(element: ET.Element) -> str:
    parts: list[str] = []
    for node in element.iter():
        lname = local_name(node.tag)
        if lname in {"t", "delText"} and node.text:
            parts.append(node.text)
        elif lname == "tab":
            parts.append("\t")
        elif lname in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def text_nodes_under(element: ET.Element) -> list[ET.Element]:
    return [node for node in element.iter() if local_name(node.tag) in {"t", "delText"}]


def replace_text_in_containers(
    root: ET.Element,
    *,
    container_names: set[str],
    find: str,
    replace: str,
    max_count: int | None = None,
) -> int:
    if not find:
        raise DocumentSkillError("replace_text requires a non-empty find string.", code="invalid_operation")

    changed = 0
    remaining = max_count
    for container in root.iter():
        if local_name(container.tag) not in container_names:
            continue
        nodes = text_nodes_under(container)
        if not nodes:
            continue
        original = "".join(node.text or "" for node in nodes)
        if find not in original:
            continue
        count = -1 if remaining is None else remaining
        updated = original.replace(find, replace, count)
        delta = original.count(find) if remaining is None else min(original.count(find), remaining)
        if delta <= 0 or updated == original:
            continue
        nodes[0].text = updated
        for node in nodes[1:]:
            node.text = ""
        changed += delta
        if remaining is not None:
            remaining -= delta
            if remaining <= 0:
                break
    return changed


def read_xml_from_zip(zip_path: Path, member: str) -> ET.Element:
    with zipfile.ZipFile(zip_path) as zf:
        try:
            data = zf.read(member)
        except KeyError as exc:
            raise DocumentSkillError(f"Missing OOXML part: {member}", code="missing_part") from exc
    return ET.fromstring(data)


def write_modified_zip(input_path: Path, output_path: Path, replacements: dict[str, bytes]) -> None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="office-documents-"))
    tmp_path = tmp_dir / output_path.name
    try:
        with zipfile.ZipFile(input_path, "r") as zin, zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            seen: set[str] = set()
            for info in zin.infolist():
                data = replacements.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                zout.writestr(info, data)
                seen.add(info.filename)
            for name, data in replacements.items():
                if name not in seen:
                    zout.writestr(name, data)
        shutil.move(str(tmp_path), str(output_path))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def numeric_part_key(name: str) -> tuple[int, str]:
    match = re.search(r"(\d+)(?=\.[^.]+$)", name)
    return (int(match.group(1)) if match else 0, name)


def parse_page_spec(spec: str | int | list[int], total_pages: int) -> list[int]:
    if isinstance(spec, int):
        values = [spec]
    elif isinstance(spec, list):
        values = spec
    elif isinstance(spec, str):
        values = []
        for chunk in spec.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start_s, end_s = chunk.split("-", 1)
                start, end = int(start_s), int(end_s)
                step = 1 if start <= end else -1
                values.extend(range(start, end + step, step))
            else:
                values.append(int(chunk))
    else:
        raise DocumentSkillError("Page spec must be an integer, list, or range string.", code="invalid_pages")

    result: list[int] = []
    for value in values:
        if value < 1 or value > total_pages:
            raise DocumentSkillError(
                f"Page {value} is out of range. Document has {total_pages} pages.",
                code="page_out_of_range",
            )
        idx = value - 1
        if idx not in result:
            result.append(idx)
    return result


def cell_ref_to_col_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha()).upper()
    if not letters:
        return 1
    value = 0
    for ch in letters:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value


def truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars] + "\n\n[Truncated by read_document.py max-chars limit]\n", True


# ─────────────────────────────────────────────────────────────
# Docling-first degradation chain (added 2026-06-22)
# ─────────────────────────────────────────────────────────────

import time
from dataclasses import dataclass, field
from enum import Enum


class BackendTier(str, Enum):
    DOCLING = "docling"
    MINERU = "mineru"  # MinerU cloud parse (added 2026-10-17; PDF/images only)
    VISION = "vision"  # macOS Apple Vision OCR (added 2026-07-02, scanned PDF fast path on M-series Macs)
    MARKITDOWN = "markitdown"
    DIRECT = "direct"


@dataclass
class ConversionResult:
    """Outcome of a single backend attempt in the docling-first degradation chain."""
    backend: str
    status: str
    elapsed: float
    text: str | None = None
    metadata: dict = field(default_factory=dict)
    reason: str | None = None
    warning: str | None = None


DEFAULT_DOCLING_TIMEOUT = 600  # raised from 300 (2026-06-24 fix: scanned PDF OCR + model load takes 5+ min on first run, 300s always times out)
HARD_DOCLING_TIMEOUT_CAP = 7200


def find_soffice() -> str | None:
    """Locate LibreOffice/soffice binary for .doc → .docx conversion (2026-06-24)."""
    for cand in ("soffice", "/opt/homebrew/bin/soffice", "/usr/local/bin/soffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(cand).exists() or shutil.which(cand):
            return cand
    return None


def convert_doc_to_docx(doc_path: Path) -> Path | None:
    """Convert Microsoft Word 97-2003 .doc to modern .docx via LibreOffice (2026-06-24).

    Why: markitdown/mammoth/python-docx only support .docx; .doc binary format requires
    LibreOffice (antiword not in PATH, catdoc not installed). Auto-conversion unblocks
    read_document.py to process legacy .doc files via docling/markitdown/direct fallbacks.
    """
    soffice = find_soffice()
    if not soffice:
        return None
    import subprocess  # module-level import is absent; same pattern as other subprocess callers
    out_dir = doc_path.parent / f".doc-convert-{doc_path.stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(doc_path)],
            timeout=120, capture_output=True, text=True,
            env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(Path.home())},
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    converted = out_dir / f"{doc_path.stem}.docx"
    return converted if converted.exists() else None
DOCLING_VENV_PYTHON = Path.home() / ".workspace" / ".venv-docling" / "bin" / "python"
DOCLING_BIN = Path.home() / ".workspace" / ".venv-docling" / "bin" / "docling"  # bash wrapper (2026-06-24 fix: docling package has no __main__, must use wrapper, not python -m docling)
VISION_SCRIPT = Path(__file__).parent.parent / "bin" / "ocr-vision.swift"  # macOS Apple Vision OCR (added 2026-07-02, scanned PDF fast path on M-series Macs)


def is_macos_arm64() -> bool:
    """Detect Apple Silicon Mac (M1+). Vision OCR only makes sense here."""
    import platform, sys
    if sys.platform != "darwin":
        return False
    try:
        import subprocess
        out = subprocess.run(["uname", "-m"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out == "arm64"
    except Exception:
        return False


def try_vision(path: Path, *, timeout: int = 60) -> tuple[str | None, dict | None, str | None]:
    """Run macOS Apple Vision OCR via Swift script.

    Only applicable for PDFs on Apple Silicon. Returns (markdown_text, metadata, warning).
    Added 2026-07-02 to fix docling 5-minute timeout on scanned EPSON Scan PDFs.
    Reference: pinned memory "docling 在 macOS M4 上的配置坑 + 修复方案".
    """
    if not VISION_SCRIPT.exists():
        return None, None, f"vision script not found at {VISION_SCRIPT}"
    if path.suffix.lower() != ".pdf":
        return None, None, "vision backend only handles PDFs"
    try:
        import subprocess
        proc = subprocess.run(
            ["swift", str(VISION_SCRIPT), str(path)],
            timeout=timeout,
            capture_output=True,
            text=True,
            env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin"},
        )
    except subprocess.TimeoutExpired:
        return None, None, f"vision timed out after {timeout}s"
    except FileNotFoundError:
        return None, None, "swift not in PATH"
    except Exception as exc:
        return None, None, f"vision failed: {type(exc).__name__}: {exc}"

    if proc.returncode != 0:
        err_lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        stderr = (err_lines[-1] if err_lines else "")[:300].strip()
        return None, None, f"vision exited with code {proc.returncode}: {stderr}"

    text = proc.stdout or ""
    if not text.strip():
        return None, None, "vision returned empty output"
    # Strip the doc-comments header (lines starting with <!--) for cleaner markdown
    lines = text.split("\n")
    clean_lines = [l for l in lines if not l.startswith("<!--")]
    clean = "\n".join(clean_lines).strip()
    if not clean:
        return None, None, "vision returned only header comments"
    return clean + "\n", {"reader": "vision"}, None


def _docling_output_degenerate(text: str) -> str | None:
    """Detect docling's silent wide-table collapse.

    docling does not fail on a wide table — it emits ONE line where the whole
    table is joined with padding spaces, exits 0, and reports success. Measured
    on a 19-column quality form: 200105 chars, of which 92 were non-whitespace.
    Anything downstream then treats a gutted document as a good conversion, which
    is how batch runs silently drop files.

    Returns a short reason string when the output looks unusable, else None.
    """
    if len(text) < 5000:
        return None
    longest = max((len(line) for line in text.splitlines()), default=0)
    if longest > 5000:
        return f"single line of {longest} chars"
    nonws = len(re.sub(r"\s", "", text))
    if nonws / max(len(text), 1) < 0.03:
        return f"{nonws} non-whitespace chars in {len(text)}"
    return None


def try_docling(path: Path, *, timeout: int) -> tuple[str | None, dict | None, str | None]:
    """Run docling on the given path via its dedicated venv.

    Returns: (markdown_text, metadata, warning) — any may be None on failure.
    """
    if not DOCLING_VENV_PYTHON.exists():
        return None, None, f"docling venv not found at {DOCLING_VENV_PYTHON}"
    import subprocess
    import tempfile
    # Scratch dir lives in the OS temp area, not next to the source file, and is
    # always removed in the finally block. Before 2026-10-28 this wrote
    # `.docling-out-<stem>/` beside every input and never cleaned it up: 380
    # stale dirs / 1.0G had accumulated in the source tree.
    out_dir = Path(tempfile.mkdtemp(prefix="docling-out-"))
    try:
        try:
            proc = subprocess.run(
                [
                    str(DOCLING_BIN),
                    str(path),
                    "--to", "md",
                    "--output", str(out_dir),
                ],
                timeout=timeout,
                capture_output=True,
                text=True,
                # inherit PATH instead of overriding: docling's OCR stack (RapidOCR/
                # torch) may resolve tools relative to the caller's PATH, and a
                # trimmed PATH made PDF runs hang on first model init
                # HF_HUB_OFFLINE=1: all docling models are cached locally
                # (~/.cache/huggingface 6.9G); without it docling probes
                # huggingface.co on every start and stalls ~5min on unreachable
                # network before falling back to cache (measured 309s -> 9.6s)
                # Proxy vars stripped: docling's httpx aborts with "Using SOCKS
                # proxy, but the 'socksio' package is not installed" whenever
                # http_proxy/https_proxy points at socks5://. docling runs fully
                # offline here, so it must not see the caller's proxy at all.
                env={
                    **{k: v for k, v in os.environ.items()
                       if k.lower() not in {"http_proxy", "https_proxy", "all_proxy"}},
                    "PYTHONUNBUFFERED": "1",
                    "HF_HUB_OFFLINE": "1",
                },
            )
        except subprocess.TimeoutExpired:
            return None, None, f"docling timed out after {timeout}s"
        except Exception as exc:
            return None, None, f"docling failed: {type(exc).__name__}: {exc}"

        if proc.returncode != 0:
            err_lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
            stderr = (err_lines[-1] if err_lines else "")[:300].strip()
            return None, None, f"docling exited with code {proc.returncode}: {stderr}"

        md_files = list(out_dir.glob("*.md"))
        if not md_files:
            return None, None, "docling produced no .md output"
        text = md_files[0].read_text("utf-8")
        if not text.strip():
            return None, None, "docling returned empty output"
        degraded = _docling_output_degenerate(text)
        if degraded:
            # Treat a collapsed table as a failure so the chain keeps going: the
            # next tiers (markitdown) return this content intact.
            return None, None, f"docling output unusable ({degraded})"
        return text, {"reader": "docling"}, None
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def write_conversion_log(output_path: Path, attempts: list) -> Path:
    """Write a .conversion.log sibling file tracing the degradation chain."""
    log_path = output_path.with_suffix(output_path.suffix + ".conversion.log")
    lines = []
    for attempt in attempts:
        elapsed_str = f"{attempt.elapsed:.1f}s"
        if attempt.status == "ok":
            lines.append(f"backend={attempt.backend}, status=ok, elapsed={elapsed_str}")
        else:
            detail = attempt.reason or ""
            if attempt.warning and attempt.warning != detail:
                detail = f"{detail} ({attempt.warning})" if detail else attempt.warning
            lines.append(f"backend={attempt.backend}, status=fail, reason={detail}, elapsed={elapsed_str}")
    log_path.write_text("\n".join(lines) + "\n", "utf-8")
    return log_path


# ─────────────────────────────────────────────────────────────
# MinerU cloud parsing backend (added 2026-10-17)
# PDF/images are parsed by the MinerU cloud API first; other formats stay local.
# Only the read chain uses this; edit_*.py are untouched.
# ─────────────────────────────────────────────────────────────

MINERU_EXTS = {
    # Documents: MinerU parses Office server-side. Widened 2026-10-28 from
    # PDF/images only — local docling degrades badly on wide tables and MinerU
    # keeps merged cells intact.
    # .html/.htm are deliberately EXCLUDED: MinerU v4 rejects them with
    # code=-10002 ("model_version 'vlm' cannot process html files") and the
    # rejection is per-BATCH, so one html file fails every other file queued
    # with it. HTML stays on the local chain.
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    # Images
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp",
}
MINERU_API_BASE = "https://mineru.net/api/v4"
MINERU_MODEL_VERSION = "vlm"          # official default is "pipeline"; vlm is recommended
MINERU_TIMEOUT = 300                  # poll timeout (s); independent from docling's 600
MINERU_POLL_INTERVAL = 3
MINERU_BATCH_LIMIT = 50               # official: <=50 upload links per file-urls/batch call
MINERU_MAX_BYTES = 200 * 1024 * 1024  # official: <=200MB per file
MINERU_CRED_FILE = Path.home() / ".hanako" / "secrets" / "mineru.token"
MINERU_NOT_SUPPORTED_REASON = (
    "file type not accepted by the MinerU backend"
)


def mineru_token() -> str | None:
    """Resolve the MinerU API token: env MINERU_TOKEN first, then credential file.

    Official SDK reads MINERU_TOKEN; the credential file is a local convenience.
    """
    tok = os.environ.get("MINERU_TOKEN")
    if tok and tok.strip():
        return tok.strip()
    try:
        if MINERU_CRED_FILE.exists():
            tok = MINERU_CRED_FILE.read_text("utf-8").strip()
            if tok:
                return tok
    except Exception:
        pass
    return None


def rewrite_media_links(markdown: str, prefix: str) -> str:
    """Rewrite image targets that point into the zip's images/ dir.

    prefix is the media dir name relative to the output file, e.g. "foo_mineru".
    Handles BOTH syntaxes MinerU emits: markdown ![alt](url) for PDF/doc, and
    HTML <img src="url"> for docx — the docx form was left unprefixed until
    2026-10-28, which produced broken image links.
    Targets containing whitespace are wrapped in angle brackets (markdown form only).
    """
    def repl(match) -> str:
        alt, url = match.group(1), match.group(2)
        if url.startswith("images/"):
            url = f"{prefix}/{url}"
        if any(ch.isspace() for ch in url):
            url = f"<{url}>"
        return f"![{alt}]({url})"

    markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, markdown)

    def repl_img(match) -> str:
        head, quoted, tail = match.group(1), match.group(2), match.group(3)
        url = quoted.strip('"')
        if url.startswith("images/"):
            url = f"{prefix}/{url}"
        return f'{head}"{url}"{tail}'

    # <img ... src="images/..." ...>
    return re.sub(r'(<img\b[^>]*?\bsrc=)("[^"]*")([^>]*>)', repl_img, markdown)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", repl, markdown)


def _mineru_curl(args: list[str], *, timeout: int = 120):
    """Run curl (urllib gets Connection-reset against mineru.net; curl is fine)."""
    import subprocess
    return subprocess.run(
        ["curl", "-g", "-sS", "--max-time", str(timeout), *args],
        capture_output=True, text=True,
    )


def _mineru_json(proc) -> dict:
    try:
        return json.loads(proc.stdout or "{}")
    except Exception:
        detail = (proc.stdout or proc.stderr or "curl failed").strip()[:200]
        return {"code": -1, "msg": detail}


def _mineru_post(path: str, *, token: str, payload: dict, timeout: int = 120) -> dict:
    proc = _mineru_curl([
        "-X", "POST", f"{MINERU_API_BASE}{path}",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Content-Type: application/json",
        "-H", "Accept: */*",
        "-d", json.dumps(payload),
    ], timeout=timeout)
    return _mineru_json(proc)


def _mineru_get(path: str, *, token: str, timeout: int = 60) -> dict:
    proc = _mineru_curl([
        f"{MINERU_API_BASE}{path}",
        "-H", f"Authorization: Bearer {token}",
        "-H", "Accept: */*",
    ], timeout=timeout)
    return _mineru_json(proc)


def _mineru_upload(url: str, local: Path, *, timeout: int = 300) -> bool:
    proc = _mineru_curl(
        ["-o", "/dev/null", "-w", "%{http_code}", "-X", "PUT", "-T", str(local), url],
        timeout=timeout,
    )
    return (proc.stdout or "").strip() == "200"


def _mineru_download(url: str, dest: Path, *, timeout: int = 300) -> bool:
    _mineru_curl(["-o", str(dest), url], timeout=timeout)
    return dest.exists() and dest.stat().st_size > 0


def _mineru_fetch_zip(zip_url: str, source: Path, media_root: Path | None,
                      total_pages=None) -> tuple[bool, str | None, dict | None, str | None]:
    """Download a result zip; take full.md text + extract images/ (not the whole zip).

    Returns (ok, text, metadata, warning).
    """
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp(prefix="mineru-"))
    zip_path = tmp_dir / "result.zip"
    try:
        if not _mineru_download(zip_url, zip_path):
            return False, None, None, "mineru result download failed"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            md_name = next((n for n in names if n.endswith("full.md")), None)
            if md_name is None:
                return False, None, None, "mineru result has no full.md"
            text = zf.read(md_name).decode("utf-8", "replace")
            warning = None
            if media_root is not None:
                media_dir = Path(media_root) / f"{source.stem}_mineru"
                for name in names:
                    if name.startswith("images/") and not name.endswith("/"):
                        dest = media_dir / name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(zf.read(name))
                text = rewrite_media_links(text, f"{source.stem}_mineru")
            else:
                warning = "mineru media not downloaded (no --output)"
        meta = {"reader": "mineru"}
        if total_pages is not None:
            meta["total_pages"] = total_pages
        return True, text, meta, warning
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def mineru_extract(files: list[Path], *, token: str | None, media_root: Path | None,
                   model_version: str = MINERU_MODEL_VERSION,
                   timeout: int = MINERU_TIMEOUT) -> list[dict]:
    """Batch MinerU cloud extraction. One verdict per input file, in input order.

    verdict = {file, status, reason, text, metadata, warning, elapsed}
    Shared by try_mineru (N=1) and read_folder (batch). Pre-checks (extension,
    size) live here so both paths get them.
    """
    results: list[dict] = [
        {"file": str(f), "status": None, "reason": None, "text": None,
         "metadata": None, "warning": None, "elapsed": 0.0, "precheck": False}
        for f in files
    ]
    t_start = time.monotonic()

    def finish() -> list[dict]:
        elapsed = round(time.monotonic() - t_start, 3)
        for r in results:
            if r["status"] is None:
                r["status"], r["reason"] = "fail", "mineru did not process this file"
            r["elapsed"] = elapsed
        return results

    if not token:
        for r in results:
            r["status"], r["reason"] = "fail", "MINERU_TOKEN not set and no credential file"
        return finish()

    pending: list[tuple[int, Path]] = []
    for idx, f in enumerate(files):
        if f.suffix.lower() not in MINERU_EXTS:
            results[idx]["status"], results[idx]["reason"] = "fail", MINERU_NOT_SUPPORTED_REASON
            results[idx]["precheck"] = True
        elif f.stat().st_size > MINERU_MAX_BYTES:
            results[idx]["status"], results[idx]["reason"] = "fail", "file exceeds 200MB"
            results[idx]["precheck"] = True
        else:
            pending.append((idx, f))

    for chunk_start in range(0, len(pending), MINERU_BATCH_LIMIT):
        chunk = pending[chunk_start:chunk_start + MINERU_BATCH_LIMIT]
        payload = {
            "files": [{"name": f.name, "data_id": f"id{idx}"} for idx, f in chunk],
            "model_version": model_version,
        }
        resp = _mineru_post("/file-urls/batch", token=token, payload=payload)
        if resp.get("code") != 0:
            reason = f"mineru submit failed: code={resp.get('code')} msg={resp.get('msg')}"
            for idx, _ in chunk:
                results[idx]["status"], results[idx]["reason"] = "fail", reason
            continue
        data = resp.get("data") or {}
        batch_id = data.get("batch_id")
        urls = data.get("file_urls") or []
        accepted: dict[str, tuple[int, Path]] = {}
        for (idx, f), url in zip(chunk, urls):
            if _mineru_upload(url, f):
                accepted[f"id{idx}"] = (idx, f)
            else:
                results[idx]["status"], results[idx]["reason"] = "fail", "mineru upload failed"
        if not batch_id or not accepted:
            continue

        deadline = time.monotonic() + timeout
        final: dict[str, dict] = {}
        timed_out = True
        while time.monotonic() < deadline:
            poll = _mineru_get(f"/extract-results/batch/{batch_id}", token=token)
            rows = ((poll.get("data") or {}).get("extract_result")) or []
            if rows and all(row.get("state") in {"done", "failed"} for row in rows):
                for row in rows:
                    final[row.get("data_id")] = row
                timed_out = False
                break
            time.sleep(MINERU_POLL_INTERVAL)
        if timed_out:
            for idx, _ in accepted.values():
                if results[idx]["status"] is None:
                    results[idx]["status"] = "fail"
                    results[idx]["reason"] = f"mineru timed out after {timeout}s"
            continue

        for did, (idx, f) in accepted.items():
            row = final.get(did)
            if row is None:
                results[idx]["status"], results[idx]["reason"] = "fail", "mineru returned no result"
                continue
            if row.get("state") != "done":
                results[idx]["status"] = "fail"
                results[idx]["reason"] = row.get("err_msg") or f"mineru state={row.get('state')}"
                continue
            zip_url = row.get("full_zip_url")
            if not zip_url:
                results[idx]["status"], results[idx]["reason"] = "fail", "mineru returned no full_zip_url"
                continue
            total_pages = (row.get("extract_progress") or {}).get("total_pages")
            ok, text, meta, warn = _mineru_fetch_zip(zip_url, f, media_root, total_pages)
            if ok:
                results[idx].update({"status": "ok", "text": text, "metadata": meta, "warning": warn})
            else:
                results[idx]["status"], results[idx]["reason"] = "fail", warn or "mineru zip fetch failed"

    return finish()


def try_mineru(path: Path, *, media_root: Path | None,
               model_version: str = MINERU_MODEL_VERSION,
               timeout: int = MINERU_TIMEOUT) -> tuple[str | None, dict | None, str | None]:
    """Run MinerU cloud extraction for a single file (N=1 wrapper over mineru_extract).

    Returns (markdown_text, metadata, warning).
    """
    token = mineru_token()
    verdicts = mineru_extract([path], token=token, media_root=media_root,
                              model_version=model_version, timeout=timeout)
    verdict = verdicts[0]
    if verdict["status"] == "ok" and verdict.get("text"):
        return verdict["text"], (verdict.get("metadata") or {}), verdict.get("warning")
    return None, None, verdict.get("reason") or "mineru failed"
