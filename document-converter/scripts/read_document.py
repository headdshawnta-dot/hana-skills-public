#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from office_common import (
    BackendTier,
    ConversionResult,
    DEFAULT_DOCLING_TIMEOUT,
    MINERU_EXTS,
    is_macos_arm64,
    try_mineru,
    try_vision,
    HARD_DOCLING_TIMEOUT_CAP,
    DocumentSkillError,
    attr_local,
    cell_ref_to_col_index,
    element_text,
    json_error,
    json_success,
    local_name,
    numeric_part_key,
    require_file,
    truncate_text,
    try_docling,
    write_conversion_log,
    write_text_or_json,
)


def try_markitdown(path: Path) -> tuple[str | None, str | None]:
    """Convert via markitdown.

    Two paths:
    1. In-process import (works when markitdown is installed for the running
       interpreter).
    2. CLI subprocess fallback (markitdown installed under a different
       interpreter, e.g. /opt/homebrew/bin/markitdown with python3.11 while
       read_document.py runs on python3.14). Keeps the backend independent
       of interpreter environment.
    """
    try:
        from markitdown import MarkItDown  # type: ignore
    except ImportError:
        pass  # fall through to CLI path below
    except Exception as exc:
        return None, f"markitdown unavailable: {exc}"
    else:
        try:
            md = MarkItDown(enable_plugins=False)
            if hasattr(md, "convert_local"):
                result = md.convert_local(str(path))
            else:
                result = md.convert(str(path))
            text = getattr(result, "text_content", None)
            if isinstance(text, str) and text.strip():
                return text, None
            if text is not None:
                return None, "markitdown returned empty content"
        except Exception as exc:
            return None, f"markitdown failed: {exc}"

    import shutil
    import subprocess

    cli = shutil.which("markitdown")
    if not cli:
        return None, "markitdown unavailable: not importable and no CLI in PATH"
    try:
        proc = subprocess.run(
            [cli, str(path)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None, "markitdown CLI timed out after 120s"
    except Exception as exc:
        return None, f"markitdown CLI failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        err_lines = [l for l in (proc.stderr or "").splitlines() if l.strip()]
        stderr = (err_lines[-1] if err_lines else "")[:300].strip()
        return None, f"markitdown CLI exited with code {proc.returncode}: {stderr}"
    text = proc.stdout or ""
    if not text.strip():
        return None, "markitdown CLI returned empty output"
    return text, None


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[cell.replace("|", "\\|").replace("\n", " ") for cell in row] for row in normalized]
    header = escaped[0]
    sep = ["---"] * width
    body = escaped[1:]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def read_doc_textutil(path: Path) -> tuple[str | None, list[str], dict]:
    """Read legacy .doc via macOS textutil (no LibreOffice dependency).

    textutil converts to plain text, losing table structure; used as last-resort
    direct backend for old binary .doc files that docling/markitdown reject.
    """
    warnings: list[str] = []
    try:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
            tmp = tf.name
        try:
            proc = subprocess.run(
                ["textutil", "-convert", "txt", "-output", tmp, str(path)],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            warnings.append("textutil timed out after 120s")
            return None, warnings, {"reader": "textutil"}
        if proc.returncode != 0:
            stderr = (proc.stderr or "")[:200].strip()
            warnings.append(f"textutil exited with code {proc.returncode}: {stderr}")
            return None, warnings, {"reader": "textutil"}
        text = Path(tmp).read_text("utf-8", errors="replace")
        Path(tmp).unlink(missing_ok=True)
        if not text.strip():
            warnings.append("textutil returned empty output")
            return None, warnings, {"reader": "textutil"}
        return f"# {path.name}\n\n{text.strip()}\n", warnings, {"reader": "textutil", "converter": "textutil"}
    except Exception as exc:
        warnings.append(f"textutil failed: {type(exc).__name__}: {exc}")
        return None, warnings, {"reader": "textutil"}


def read_docx(path: Path) -> tuple[str, list[str], dict]:
    warnings: list[str] = []
    meta: dict = {"reader": "direct-ooxml"}
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            raise DocumentSkillError("DOCX is missing word/document.xml", code="invalid_docx")
        root = ET.fromstring(zf.read("word/document.xml"))
        lines: list[str] = [f"# {path.name}", ""]
        paragraph_count = 0
        table_count = 0

        body = next((node for node in root.iter() if local_name(node.tag) == "body"), root)
        for child in list(body):
            lname = local_name(child.tag)
            if lname == "p":
                text = element_text(child).strip()
                if not text:
                    continue
                paragraph_count += 1
                style = ""
                for node in child.iter():
                    if local_name(node.tag) == "pStyle":
                        style = attr_local(node, "val", "") or ""
                        break
                if style.lower().startswith("heading"):
                    level = "".join(ch for ch in style if ch.isdigit()) or "2"
                    lines.append(f"{'#' * max(2, min(6, int(level) + 1))} {text}")
                else:
                    lines.append(text)
                lines.append("")
            elif lname == "tbl":
                table_count += 1
                rows: list[list[str]] = []
                for tr in child.iter():
                    if local_name(tr.tag) != "tr":
                        continue
                    row: list[str] = []
                    for tc in list(tr):
                        if local_name(tc.tag) == "tc":
                            row.append(element_text(tc).strip())
                    if row:
                        rows.append(row)
                if rows:
                    lines.append(f"## Table {table_count}")
                    lines.append("")
                    lines.append(markdown_table(rows))
                    lines.append("")

        comments = []
        if "word/comments.xml" in names:
            croot = ET.fromstring(zf.read("word/comments.xml"))
            for comment in croot.iter():
                if local_name(comment.tag) == "comment":
                    text = element_text(comment).strip()
                    if text:
                        author = attr_local(comment, "author", "") or ""
                        comments.append({"author": author, "text": text})
            if comments:
                lines.append("## Comments")
                lines.append("")
                for i, comment in enumerate(comments, 1):
                    prefix = f"{i}. "
                    if comment["author"]:
                        prefix += f"{comment['author']}: "
                    lines.append(prefix + comment["text"])
                lines.append("")

        media = sorted(name for name in names if name.startswith("word/media/"))
        if media:
            lines.append("## Embedded Media")
            lines.append("")
            lines.extend(f"- {name}" for name in media)
            lines.append("")

        meta.update({"paragraphs": paragraph_count, "tables": table_count, "comments": len(comments), "media": len(media)})
        return "\n".join(lines).strip() + "\n", warnings, meta


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    values: list[str] = []
    for si in root:
        if local_name(si.tag) != "si":
            continue
        values.append(element_text(si))
    return values


def workbook_sheets(zf: zipfile.ZipFile) -> list[tuple[str, str]]:
    rels: dict[str, str] = {}
    try:
        rel_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        for rel in rel_root:
            rid = rel.attrib.get("Id")
            target = rel.attrib.get("Target")
            if rid and target:
                if target.startswith("/"):
                    target = target.lstrip("/")
                elif not target.startswith("xl/"):
                    target = "xl/" + target
                rels[rid] = target
    except KeyError:
        pass

    root = ET.fromstring(zf.read("xl/workbook.xml"))
    sheets: list[tuple[str, str]] = []
    for sheet in root.iter():
        if local_name(sheet.tag) != "sheet":
            continue
        name = sheet.attrib.get("name") or f"Sheet{len(sheets) + 1}"
        rid = attr_local(sheet, "id", "")
        target = rels.get(rid or "", f"xl/worksheets/sheet{len(sheets) + 1}.xml")
        sheets.append((name, target))
    return sheets


def read_xlsx(path: Path, max_rows: int, max_cols: int) -> tuple[str, list[str], dict]:
    warnings: list[str] = []
    # 0 means "no limit", matching --max-chars. Before 2026-10-28 a 0 here silently
    # produced an empty sheet, and README advised --max-rows 0 for full ledgers.
    eff_rows = max_rows if max_rows > 0 else 1_000_000
    eff_cols = max_cols if max_cols > 0 else 1000
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        if "xl/workbook.xml" not in names:
            raise DocumentSkillError("XLSX is missing xl/workbook.xml", code="invalid_xlsx")
        strings = shared_strings(zf)
        sheets = workbook_sheets(zf)
        lines = [f"# {path.name}", ""]
        sheet_meta = []
        for sheet_name, member in sheets:
            if member not in names:
                warnings.append(f"Sheet part missing: {member}")
                continue
            root = ET.fromstring(zf.read(member))
            rows_out: list[list[str]] = []
            formula_count = 0
            # Track what the limits throw away so the caller is told, instead of
            # handing back a silently shortened sheet. Only rows that actually
            # carry cells count: sheets often have thousands of blank formatting
            # rows, and counting those produced bogus "truncated" warnings on
            # sheets whose real content fit easily.
            highest_row_index = 0
            cols_dropped = 0
            for row in root.iter():
                if local_name(row.tag) != "row":
                    continue
                row_index = int(row.attrib.get("r", len(rows_out) + 1))
                if row_index > eff_rows:
                    if any(local_name(c.tag) == "c" for c in row):
                        highest_row_index = max(highest_row_index, row_index)
                    continue
                values = [""] * eff_cols
                for cell in list(row):
                    if local_name(cell.tag) != "c":
                        continue
                    ref = cell.attrib.get("r", "")
                    col = cell_ref_to_col_index(ref) - 1
                    if col < 0 or col >= eff_cols:
                        cols_dropped += 1
                        continue
                    cell_type = cell.attrib.get("t", "")
                    value = ""
                    formula = ""
                    for part in list(cell):
                        lname = local_name(part.tag)
                        if lname == "f":
                            formula = part.text or ""
                        elif lname == "v":
                            value = part.text or ""
                        elif lname == "is":
                            value = element_text(part)
                    if formula:
                        formula_count += 1
                        display = "=" + formula
                        if value:
                            display += f" [{value}]"
                    elif cell_type == "s" and value:
                        try:
                            display = strings[int(value)]
                        except Exception:
                            display = value
                    else:
                        display = value
                    values[col] = display
                if any(values):
                    while rows_out and len(rows_out) < row_index - 1:
                        rows_out.append([""] * eff_cols)
                    rows_out.append(values)
            if max_rows > 0 and highest_row_index > max_rows:
                warnings.append(
                    f"sheet '{sheet_name}': truncated at --max-rows={max_rows}, "
                    f"source has {highest_row_index} row(s); raise --max-rows to see the rest"
                )
            if max_cols > 0 and cols_dropped:
                warnings.append(
                    f"sheet '{sheet_name}': {cols_dropped} cell(s) beyond --max-cols={max_cols} "
                    f"dropped; raise --max-cols to see them"
                )
            lines.append(f"## Sheet: {sheet_name}")
            lines.append("")
            if rows_out:
                used_width = 0
                for row in rows_out:
                    for idx, value in enumerate(row):
                        if value:
                            used_width = max(used_width, idx + 1)
                used_width = max(1, used_width)
                lines.append(markdown_table([row[:used_width] for row in rows_out[:eff_rows]]))
            else:
                lines.append("[Empty sheet]")
            lines.append("")
            sheet_meta.append({"name": sheet_name, "part": member, "rows_shown": len(rows_out[:eff_rows]), "formulas_shown": formula_count})
        return "\n".join(lines).strip() + "\n", warnings, {"reader": "direct-ooxml", "sheets": sheet_meta}


def read_pptx(path: Path) -> tuple[str, list[str], dict]:
    warnings: list[str] = []
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        slide_names = sorted([name for name in names if name.startswith("ppt/slides/slide") and name.endswith(".xml")], key=numeric_part_key)
        if not slide_names:
            raise DocumentSkillError("PPTX has no slide XML parts.", code="invalid_pptx")
        lines = [f"# {path.name}", ""]
        for i, slide_name in enumerate(slide_names, 1):
            root = ET.fromstring(zf.read(slide_name))
            paragraphs = []
            for node in root.iter():
                if local_name(node.tag) == "p":
                    text = element_text(node).strip()
                    if text:
                        paragraphs.append(text)
            lines.append(f"## Slide {i}")
            lines.append("")
            lines.extend(f"- {text}" for text in paragraphs)
            if not paragraphs:
                lines.append("[No extracted text]")
            notes_name = f"ppt/notesSlides/notesSlide{i}.xml"
            if notes_name in names:
                nroot = ET.fromstring(zf.read(notes_name))
                notes = [element_text(node).strip() for node in nroot.iter() if local_name(node.tag) == "p" and element_text(node).strip()]
                if notes:
                    lines.append("")
                    lines.append("Notes:")
                    lines.extend(f"- {text}" for text in notes)
            lines.append("")
        return "\n".join(lines).strip() + "\n", warnings, {"reader": "direct-ooxml", "slides": len(slide_names)}


def read_pdf(path: Path) -> tuple[str, list[str], dict]:
    warnings: list[str] = []
    try:
        import pdfplumber  # type: ignore
        lines = [f"# {path.name}", ""]
        tables = 0
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, 1):
                text = page.extract_text() or ""
                lines.append(f"## Page {i}")
                lines.append("")
                lines.append(text.strip() or "[No extracted text]")
                try:
                    page_tables = page.extract_tables() or []
                except Exception:
                    page_tables = []
                for table in page_tables:
                    clean = [[("" if cell is None else str(cell)) for cell in row] for row in table if row]
                    if clean:
                        tables += 1
                        lines.append("")
                        lines.append(f"Table {tables}:")
                        lines.append(markdown_table(clean))
                lines.append("")
        return "\n".join(lines).strip() + "\n", warnings, {"reader": "pdfplumber", "tables": tables}
    except Exception as exc:
        warnings.append(f"pdfplumber unavailable or failed: {exc}")

    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(path))
        lines = [f"# {path.name}", ""]
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            lines.append(f"## Page {i}")
            lines.append("")
            lines.append(text.strip() or "[No extracted text]")
            lines.append("")
        return "\n".join(lines).strip() + "\n", warnings, {"reader": "pypdf", "pages": len(reader.pages)}
    except Exception as exc:
        warnings.append(f"pypdf unavailable or failed: {exc}")

    raise DocumentSkillError(
        "PDF reading requires MarkItDown, pdfplumber, or pypdf.",
        code="missing_pdf_reader",
        details={"warnings": warnings},
    )


def read_document(path: Path, args: argparse.Namespace) -> dict:
    # media_root is the directory the output file lives in; mineru lands images at
    # {media_root}/{stem}_mineru/ and rewrites links relative to it. None in stdout mode.
    media_root = Path(args.output).expanduser().resolve().parent if args.output else None
    winning, attempts = run_degradation_chain(
        path,
        args.backend,
        docling_timeout=args.docling_timeout,
        max_rows=args.max_rows,
        max_cols=args.max_cols,
        media_root=media_root,
    )

    if winning is None:
        raise DocumentSkillError(
            "All backends failed for conversion.",
            code="all_backends_failed",
            details={
                "attempts": [
                    {"backend": a.backend, "status": a.status, "reason": a.reason, "elapsed": a.elapsed}
                    for a in attempts
                ]
            },
        )

    text, truncated = truncate_text(winning.text, args.max_chars)
    warnings: list[str] = []
    if winning.warning:
        warnings.append(winning.warning)
    if truncated:
        warnings.append("content truncated by max-chars")
    # Warn only when docling was actually attempted and failed and the winner is
    # something else. mineru winning at the head of the chain must NOT trigger this.
    docling_failed = any(a.backend == BackendTier.DOCLING.value and a.status == "fail" for a in attempts)
    if docling_failed and winning.backend != BackendTier.DOCLING.value:
        warnings.append(
            f"docling failed; fell back to {winning.backend} (lower fidelity)"
        )

    return {
        "path": str(path),
        "format": path.suffix.lower().lstrip("."),
        "reader": winning.backend,
        "markdown": text,
        "warnings": warnings,
        "metadata": winning.metadata,
        "attempts": [
            {"backend": a.backend, "status": a.status, "reason": a.reason, "elapsed": a.elapsed}
            for a in attempts
        ],
    }


def run_degradation_chain(
    path: Path,
    backend: str,
    *,
    docling_timeout: int,
    max_rows: int,
    max_cols: int,
    media_root: Path | None = None,
) -> tuple:
    """Run the mineru-first degradation chain.

    Returns: (winning_result, all_attempts). winning is None if all tiers failed.
    """
    ext = path.suffix.lower()  # defined before tiers: used by the mineru pre-filter below

    if backend == "auto":
        # Every format in MINERU_EXTS goes to MinerU first (2026-10-28: widened from
        # PDF/images; HTML stays local because MinerU rejects it per-batch). MinerU is a first ATTEMPT, not a filter — anything it cannot
        # handle (oversize, unsupported type, network, quota) falls through to the
        # local chain below. macOS Apple Silicon keeps Vision after docling for fast
        # scanned-PDF OCR (2026-07-02).
        if is_macos_arm64():
            tiers = [BackendTier.MINERU, BackendTier.DOCLING, BackendTier.VISION, BackendTier.MARKITDOWN, BackendTier.DIRECT]
        else:
            tiers = [BackendTier.MINERU, BackendTier.DOCLING, BackendTier.MARKITDOWN, BackendTier.DIRECT]
        # Extension pre-filter applies to the auto chain only (explicit backends skip it).
        tiers = [t for t in tiers if not (t == BackendTier.MINERU and ext not in MINERU_EXTS)]
    elif backend == "mineru":
        tiers = [BackendTier.MINERU]
    elif backend == "local":
        # local-only chain (no mineru): used as the fallback for failed cloud files
        if is_macos_arm64():
            tiers = [BackendTier.DOCLING, BackendTier.VISION, BackendTier.MARKITDOWN, BackendTier.DIRECT]
        else:
            tiers = [BackendTier.DOCLING, BackendTier.MARKITDOWN, BackendTier.DIRECT]
    elif backend == "docling":
        tiers = [BackendTier.DOCLING]
    elif backend == "vision":
        # explicit vision (rare; mostly for testing)
        tiers = [BackendTier.VISION]
    elif backend == "markitdown":
        tiers = [BackendTier.MARKITDOWN]
    elif backend == "direct":
        tiers = [BackendTier.DIRECT]
    else:
        raise DocumentSkillError(f"Unknown backend: {backend}", code="invalid_backend")

    timeout = min(max(1, docling_timeout), HARD_DOCLING_TIMEOUT_CAP)
    attempts: list[ConversionResult] = []

    for tier in tiers:
        start = time.monotonic()
        try:
            if tier == BackendTier.MINERU:
                text, meta, warning = try_mineru(path, media_root=media_root)
            elif tier == BackendTier.DOCLING:
                text, meta, warning = try_docling(path, timeout=timeout)
            elif tier == BackendTier.VISION:
                text, meta, warning = try_vision(path, timeout=min(timeout, 600))
            elif tier == BackendTier.MARKITDOWN:
                text, warning = try_markitdown(path)
                meta = {"reader": "markitdown"} if text else None
            elif tier == BackendTier.DIRECT:
                if ext == ".docx":
                    text, warnings_list, meta = read_docx(path)
                elif ext == ".doc":
                    text, warnings_list, meta = read_doc_textutil(path)
                elif ext in {".xlsx", ".xlsm"}:
                    text, warnings_list, meta = read_xlsx(path, max_rows, max_cols)
                elif ext == ".pptx":
                    text, warnings_list, meta = read_pptx(path)
                elif ext == ".pdf":
                    text, warnings_list, meta = read_pdf(path)
                else:
                    raise DocumentSkillError(f"Unsupported file extension: {ext}", code="unsupported_format")
                warning = "; ".join(warnings_list) if warnings_list else None
        except Exception as exc:
            elapsed = time.monotonic() - start
            attempts.append(ConversionResult(
                backend=tier.value, status="fail",
                elapsed=elapsed, reason=f"{type(exc).__name__}: {exc}",
            ))
            continue

        elapsed = time.monotonic() - start
        if text and text.strip():
            attempts.append(ConversionResult(
                backend=tier.value, status="ok",
                elapsed=elapsed, text=text, metadata=meta or {}, warning=warning,
            ))
            return attempts[-1], attempts
        else:
            attempts.append(ConversionResult(
                backend=tier.value, status="fail",
                elapsed=elapsed,
                # prefer the backend's real reason (e.g. "vision backend only
                # handles PDFs", "docling produced no .md output") over the
                # generic fallback, so .conversion.log and --format json show
                # the actual cause instead of a misleading "empty output"
                reason=warning if warning else "empty output",
                warning=warning,
            ))

    return None, attempts


def main() -> int:
    parser = argparse.ArgumentParser(description="Read PDF, DOCX, XLSX, PPTX, and other files via mineru-first degradation chain (PDF/images go to the MinerU cloud first).")
    parser.add_argument("input", help="Input document path.")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Output format.")
    parser.add_argument("--output", help="Optional output file. A .conversion.log sibling is written when the chain has any failures.")
    parser.add_argument("--backend", choices=["auto", "mineru", "local", "docling", "vision", "markitdown", "direct"], default="auto", help="Conversion backend. auto = mineru-first chain for PDF/images, docling-first otherwise (recommended). local = local-only chain (no mineru). mineru = MinerU cloud only (PDF/images). On macOS Apple Silicon, auto/local also try Apple Vision OCR for scanned PDFs.")
    parser.add_argument("--docling-timeout", type=int, default=DEFAULT_DOCLING_TIMEOUT, help=f"Docling parse timeout in seconds (default {DEFAULT_DOCLING_TIMEOUT}, hard cap {HARD_DOCLING_TIMEOUT_CAP}).")
    parser.add_argument("--max-chars", type=int, default=200000, help="Maximum markdown characters to emit. 0 disables truncation.")
    parser.add_argument("--max-rows", type=int, default=80, help="Maximum rows per sheet for direct XLSX fallback.")
    parser.add_argument("--max-cols", type=int, default=30, help="Maximum columns per sheet for direct XLSX fallback.")
    args = parser.parse_args()

    try:
        path = require_file(args.input)
        result = read_document(path, args)
        if args.format == "json":
            write_text_or_json(args.output, json_success(**result))
        else:
            text = result["markdown"]
            if result["warnings"]:
                text += "\n\n## Reader Warnings\n\n" + "\n".join(f"- {w}" for w in result["warnings"]) + "\n"
            write_text_or_json(args.output, text)
            # Write .conversion.log when the chain had any failures (i.e. fell through a tier)
            failed_attempts = [a for a in result.get("attempts", []) if a["status"] == "fail"]
            if failed_attempts and args.output:
                output_path = Path(args.output).expanduser().resolve()
                log_attempts = [
                    ConversionResult(
                        backend=a["backend"],
                        status=a["status"],
                        elapsed=a["elapsed"],
                        reason=a.get("reason"),
                    )
                    for a in result.get("attempts", [])
                ]
                # The winning attempt is already in result["attempts"] (status=ok);
                # do not append a synthetic winner (it used to duplicate it in the log).
                write_conversion_log(output_path, log_attempts)
        return 0
    except Exception as exc:
        write_text_or_json(args.output, json_error(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
