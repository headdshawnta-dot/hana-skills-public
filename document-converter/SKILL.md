---
name: document-converter
description: "Convert PDF, DOCX, XLSX, PPTX, HTML, images, and other document formats to Markdown. Use when someone says: read this file, convert to Markdown, parse PDF, ingest into wiki, process a document. 读取、转换、入库任何文档格式时必须使用。PDF/图片优先走 MinerU 云端。"
compatibility: "PDF/images go to the MinerU cloud first (needs network + MINERU_TOKEN; falls back to local when unavailable). Docling (venv at ~/.workspace/.venv-docling) handles the rest and is the local fallback. MarkItDown is the optional fallback. DOCX/XLSX/PPTX edits use python-docx/openpyxl/python-pptx directly (no docling). PDF edits use pypdf. No OfficeCLI, Microsoft Office, LibreOffice, or GUI viewer is required."
metadata:
  default-enabled: true
  replaces: office-documents
---

# Document Converter

## Philosophy

All documents entering the wiki go through a **mineru-first degradation chain**:

```
1. MinerU cloud (PDF/images only, high quality)               ★ added 2026-10-17
   ↓ on failure / non-PDF-image
2. Docling (default local, high quality)
   ↓ on failure
3. Apple Vision OCR (macOS Apple Silicon only, scanned PDFs)  ★ added 2026-07-02
   ↓ on failure
4. MarkItDown (fallback, lightweight)
   ↓ on failure
5. Format-specific direct readers (last resort, lowest quality)
```

Non-PDF/image files skip tier 1 entirely (no cloud call).

Failure triggers (any one of):
- Parse exception
- Empty output
- Timeout (default `--docling-timeout 600s`, hard cap 2h)
- Unsupported format

Each fallback writes a `.conversion.log` sibling file so the wiki can trace conversion provenance. **降级链是后台实现细节，不是用户动作**——用户选"读一个文件"。

## When to use this skill

Trigger when the user says:
- read this file / 读一下这个文件
- convert to Markdown / 转成 Markdown
- parse PDF / 解析 PDF
- ingest into wiki / 入库 / 进 wiki
- process a document / 处理这个文档
- OCR a scanned PDF

## Backend 1: Docling (default)

### CLI

```bash
~/.workspace/.venv-docling/bin/docling input.pdf --to md --output ./out/
~/.workspace/.venv-docling/bin/docling input.docx --to md
~/.workspace/.venv-docling/bin/docling input.html --from html --to md
~/.workspace/.venv-docling/bin/docling image.png --to md  # OCR
```

### Python API

```python
from docling.document_converter import DocumentConverter
converter = DocumentConverter()
result = converter.convert("file.pdf")
md = result.document.export_to_markdown()
```

### Capabilities

- PDF: layout, table structure, formulas, reading order, OCR
- DOCX / XLSX / PPTX: structural conversion
- HTML / EPUB: parsing with depth-handling
- Images (PNG/JPEG/TIFF): OCR via embedded OCR layer
- Audio (WAV/MP3): ASR (with `asr` extra)
- Email (EML/MSG), XBRL, JATS, USPTO patents

### Prerequisites

- `~/.workspace/.venv-docling/bin/python` must exist
- Package: `docling >= 2.0` (currently 2.102.0)
- Model deps: `docling-ibm-models`, `docling-parse`, `torch`, `pypdfium2`
- Install: hand-rolled venv with bash wrapper at `~/.local/bin/docling`

### Failure handling

If docling fails (any trigger from Philosophy section), automatically fall through to MarkItDown. Write to `.conversion.log`:

```
backend=docling, status=fail, reason=<exception|empty|timeout|unsupported>, elapsed=<Xs>, falling through to markitdown
```

See `references/docling.md` for OCR engine selection, model choices, and platform notes.

## Backend 2: MarkItDown (first fallback)

### CLI

```bash
markitdown input.pdf > output.md
markitdown input.docx -o output.md
```

### Python API

```python
from markitdown import MarkItDown
md = MarkItDown()
result = md.convert("file.pdf")
md_text = result.text_content
```

### Extras (install on demand)

```bash
pip install "markitdown[pdf]"
pip install "markitdown[docx]"
pip install "markitdown[all]"
```

### Capabilities vs docling

- ✓ Fast, lightweight (~70KB wheel, no ML deps)
- ✓ Office → Markdown quick
- ✗ No PDF layout / table structure
- ✗ No formula recognition
- ✗ Weak OCR (needs external OCR tool)
- ✗ No reading-order analysis

### Failure handling

If markitdown fails, automatically fall through to format-specific readers. Write to `.conversion.log`:

```
backend=markitdown, status=fail, reason=<...>, elapsed=<Xs>, falling through to direct reader
```

See `references/markitdown.md` for extras, version compatibility, and known bugs.

## Backend 3: Format-specific direct readers (last resort)

| Format | Library | Function |
|--------|---------|----------|
| DOCX | stdlib (zipfile + ElementTree) | paragraph + table extraction |
| XLSX | stdlib (zipfile + ElementTree) | sheet + cell extraction |
| PPTX | stdlib (zipfile + ElementTree) | slide + shape extraction |
| PDF | pdfplumber → pypdf | text + table extraction |

Note: the direct Office readers are **standard-library only** (no python-docx /
openpyxl / python-pptx — those are used by the *edit* scripts). The direct PDF
reader needs pdfplumber or pypdf.

These produce the **lowest fidelity** output. Reserved for when both docling and markitdown fail. Output is plain text / simple tables; no layout, no formulas, no OCR.

If all three backends fail, error out with full conversion log preserved.

## MinerU cloud (default for PDF/images)

Since 2026-10-17, PDFs and images go to the MinerU cloud API first. Everything
else stays local. See `references/mineru.md` for the full API, limits and error codes.

- **Token**: `MINERU_TOKEN` env var, or `~/.hanako/secrets/mineru.token`. Missing → silent fallback to the local chain.
- **Formats**: PDF + images (png/jpg/jpeg/jp2/webp/gif/bmp). Other formats never call the cloud.
- **Batching**: local files go through `POST /api/v4/file-urls/batch` (≤50 links per call); one file = a batch of one.
- **Model**: `vlm`.
- **Failure**: any non-zero code (e.g. `-60005` size, `-60006` pages, `-60018` daily task limit) fails the tier and falls through.

### Folder entry: `read_folder.py`

```bash
python3 scripts/read_folder.py INPUT_DIR --output-dir OUT [--backend auto]
```

PDF/images → MinerU cloud (batched); everything else → local chain. One `.md` per
input file, media under `{stem}_mineru/`, plus `_summary.json` / `_summary.md`.

### Local escape hatch

`--backend local` runs the local-only chain (`docling → vision → markitdown → direct`,
no MinerU) — use it offline or to avoid the cloud. `--backend docling` forces the
single docling backend.

## Environment Preflight

Before invoking conversion, check capability availability:

```bash
node scripts/check_env.mjs --capability read-default
```

See `references/preflight.md` for the full capability table and what each one checks.

## Reading Workflow

### Unified entry: `read_document.py`

```bash
python3 scripts/read_document.py input.pdf \
    --backend auto \          # auto | mineru | local | docling | vision | markitdown | direct
    --output output.md \
    --docling-timeout 600     # seconds, hard cap 7200
    --format markdown
```

`--backend auto` (default) runs the degradation chain. `--backend X` forces a specific backend and skips fallback.

### Behavior

1. For PDF/images, call MinerU cloud first (`try_mineru`). Non-PDF/image skips this tier.
2. On failure (or non-PDF/image), fall through: Docling → Vision (macOS arm64) → MarkItDown → direct readers.
3. `.conversion.log` is written **only when the chain had at least one failure and `--output` is set** (markdown mode). A clean first-tier success writes no log. The log lists every attempt once (the winning attempt included).
4. On total failure: error out with the full attempt list.

### Timeout behavior

- `--docling-timeout` default = 600s (10 min)
- Hard cap = 7200s (2h) regardless of parameter
- Timeout triggers fallback, not abort
- MinerU polling has its own 300s timeout (independent of docling's)

## Editing Workflow

**Editing bypasses the degradation chain.** DOCX/XLSX/PPTX edits use python-docx / openpyxl / python-pptx directly. PDF edits use pypdf. These operations are deterministic and don't benefit from docling.

```bash
python3 scripts/edit_docx.py input.docx output.docx --ops ops.json
python3 scripts/edit_xlsx.py input.xlsx output.xlsx --ops ops.json
python3 scripts/edit_pptx.py input.pptx output.pptx --ops ops.json
python3 scripts/edit_pdf.py input.pdf output.pdf --ops ops.json
```

Edit operations are documented in `references/docx.md`, `references/xlsx.md`, `references/pptx.md`, `references/pdf.md`.

## Verification

After any conversion:

1. Output file exists, non-empty.
2. If the chain had failures, `.conversion.log` exists and shows which backend succeeded (a clean first-tier success writes no log).
3. Read output and confirm semantic fidelity (not just non-empty).
4. If docling was tried and failed, surface this to the user — quality may be lower.

After any edit:

1. Output file exists, non-empty.
2. Read output and verify the requested change.
3. Check unrelated content preserved.
4. For XLSX formulas: written but not recalculated locally — say so.

## macOS Apple Silicon Optimization (since 2026-07-02)

On macOS M1/M2/M3/M4 (Apple Silicon), the auto degradation chain inserts **Apple Vision OCR** between docling and MarkItDown. This is for scanned PDFs where docling's default RapidOCR backend hangs (5+ minutes timeout per file) while Apple's native Vision framework completes in 1-2 seconds per page.

- **Trigger**: `--backend auto` on a PDF on darwin/arm64
- **Implementation**: `~/.hanako/skills/document-converter/bin/ocr-vision.swift` (PDFKit + Vision framework)
- **Why needed**: docling's macOS default is RapidOCR (slow on M-series). To skip macOS Vision and force the original chain, use `--backend docling`, `--backend markitdown`, or `--backend local` explicitly.
- **Manual trigger**: `swift ~/.hanako/skills/document-converter/bin/ocr-vision.swift <file.pdf>` produces plain-text output in 1-2s per page.
- **Reference**: see pinned memory "docling 在 macOS M4 上的配置坑 + 修复方案" for full history.

## Limitations

Cannot handle:

- Pixel-perfect layout repair
- Encrypted / password-protected files
- PDF arbitrary text replacement (PDF text is drawing instructions)
- Macros, VBA, complex PowerPoint animations
- Excel pivot table authoring, slicers, external links

For these, explain the specific limitation and suggest the smallest safe alternative.

## References

Read on demand:
- `references/preflight.md` — capability table and what each one checks
- `references/mineru.md` — MinerU cloud API, limits, error codes, token
- `references/docling.md` — docling notes, model choices, OCR engines
- `references/markitdown.md` — markitdown extras, security, known bugs
- `references/docx.md` — DOCX editing details
- `references/xlsx.md` — XLSX editing details
- `references/pptx.md` — PPTX editing details
- `references/pdf.md` — PDF editing details
- `references/licenses.md` — dependency licensing

## 变更记录

- 2026-10-17 — 读链接入 MinerU 云端：PDF/图片优先走 `mineru.net`（`try_mineru` / `mineru_extract`），失败回退本地链；新增 `--backend mineru|local`、`scripts/read_folder.py`、`references/mineru.md`；修正 timeout 默认值文档（300s→600s）、direct 读取器依赖说明、`.conversion.log` 触发条件说明。
- 2026-07-02 — macOS Apple Silicon 插入 Apple Vision OCR（扫描 PDF 快路径）。
- 2026-06-24 — docling 超时默认值 300→600s；`.doc` 经 LibreOffice 转换。
- 2026-06-22 — docling-first 降级链建立。
