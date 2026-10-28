---
name: document-converter
version: "2026.10.28.1"
description: "Convert PDF, DOCX, XLSX, PPTX, HTML, images, and other document formats to Markdown. Use when someone says: read this file, convert to Markdown, parse PDF, ingest into wiki, process a document. 读取、转换、入库任何文档格式时必须使用。PDF/图片优先走 MinerU 云端。"
compatibility: "Every supported format (PDF, images, doc/docx/ppt/pptx/xls/xlsx) goes to the MinerU cloud first (needs network + MINERU_TOKEN; falls back to local when unavailable). HTML/EPUB stay local by design. Docling (venv at ~/.workspace/.venv-docling) is the local fallback. MarkItDown is the optional fallback. DOCX/XLSX/PPTX edits use python-docx/openpyxl/python-pptx directly (no docling). PDF edits use pypdf. No OfficeCLI, Microsoft Office, LibreOffice, or GUI viewer is required."
metadata:
  default-enabled: true
  replaces: office-documents
---

# Document Converter

## Philosophy

All documents entering the wiki go through a **mineru-first degradation chain**:

```
1. MinerU cloud (all formats, highest quality)                ★ widened 2026-10-28
   ↓ on failure / oversize / unsupported / quota / no token
2. Docling (default local)
   ↓ on failure
3. Apple Vision OCR (macOS Apple Silicon only, scanned PDFs)  ★ added 2026-07-02
   ↓ on failure
4. MarkItDown (fallback, lightweight)
   ↓ on failure
5. Format-specific direct readers (last resort, lowest quality)
```

Every supported format enters at tier 1 — PDF, images, Office (doc/docx/ppt/pptx/
xls/xlsx) and HTML. MinerU is a first *attempt*, not a filter: any failure falls
through to the local chain, which is unchanged.

Until 2026-10-28 Office files skipped tier 1 on the assumption that "local docling
handles Office well". That assumption broke on wide tables — docling flattens a
19-column form into one line of padding while MinerU keeps `colspan` intact, so a
file that used to degrade silently now comes back correct.

Failure triggers (any one of):
- Parse exception
- Empty output
- Timeout (default `--docling-timeout 600s`, hard cap 2h)
- Unsupported format

One conversion writes at most one `.conversion.log` sibling file (markdown mode with `--output`, and only when at least one tier failed); each line inside it records one tier attempt. **降级链是后台实现细节，不是用户动作**——用户选"读一个文件"。

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

If every backend fails, error out with the attempt list in the error payload. Note: no `.conversion.log` is written in that case.

## MinerU cloud (default for PDF/images)

Since 2026-10-17, PDFs and images go to the MinerU cloud API first. Everything
else stays local. See `references/mineru.md` for the full API, limits and error codes.

- **Token**: `MINERU_TOKEN` env var, or `~/.hanako/secrets/mineru.token`. Missing → silent fallback to the local chain.
- **Formats**: PDF, images (png/jpg/jpeg/jp2/webp/gif/bmp) and Office (doc/docx/ppt/pptx/xls/xlsx). HTML/HTM are excluded because MinerU v4 rejects them with `-10002` and the rejection applies to the whole batch.
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

1. For every format in `MINERU_EXTS`, call MinerU cloud first (`try_mineru`). Formats outside that set skip this tier.
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

- 2026-10-28（夜）— 独立审查（hanako + butter）发现的问题修正：(1) `MINERU_EXTS` 移除 `.html`/`.htm`，MinerU v4 拒收 html（`-10002`）且拒绝是整批生效，一个 html 会拖垮同批所有文件；(2) `read_xlsx` 补 `max_rows<=0` / `max_cols<=0` 的「不限」语义，此前 `--max-rows 0` 会输出空表（README 却建议用它取全量）；(3) 新增 `_docling_output_degenerate()` 质量门，单行超 5000 字符或非空白占比低于 3% 的产物判为 unusable 并继续降级 —— 此前无 token 时宽表 docx 会以 `status=ok` 交付 200KB 空格，内容全丢；(4) `rewrite_media_links` 支持 HTML `<img src>`，修复 docx 图片链接缺前缀（pdf/doc 用 markdown 语法故无此问题）；(5) `read_folder` 预检失败改为回落本地链，不再硬失败；(6) `read_folder` 的 `reader` 字段改报实际胜出后端，并保留 `mineru_status`/`mineru_reason`；(7) 空白 sheet 不再发假截断警告（只统计带单元格的行）；(8) SKILL.md/README 陈旧表述对齐（compatibility、Formats、For PDF/images、log 写入条件、全链失败不写 log、格式表三行、排障法已失效）。
- 2026-10-28（晚）— 降级链入口扩到全格式：`MINERU_EXTS` 从 PDF/图片扩到含 doc/docx/ppt/pptx/xls/xlsx，`MINERU_NOT_SUPPORTED_REASON` 文案同步。MinerU 从「仅 PDF/图片」变成「所有格式的第一尝试」，失败由原有本地链兜底（docling → Vision → markitdown → direct）。依据：实测 19 列质量单，docling 输出单行 1130 万字符且内容被截断，MinerU 输出结构完整且 `colspan` 保留。超 200MB、额度耗尽、网络不通、无 token 均正常回落本地。
- 2026-10-28（下午补充）— 处理独立审查发现的四项：(1) `try_docling` 产物目录从 `path.parent/.docling-out-<stem>` 改为 `tempfile.mkdtemp()` + `finally` 里 `shutil.rmtree`，源目录不再被污染（存量已清：380 个目录 / 1.0G）；(2) `read_xlsx` 的 `--max-rows` / `--max-cols` 截断补 `## Reader Warnings` 与 log 提示；(3) `try_vision` 超时上限 `min(timeout, 60)` → `min(timeout, 600)`，修复超 50 页扫描件永远走不到 OCR 的问题；(4) `convert_doc_to_docx` 补 `import subprocess`（此前首次调用必抛 NameError 并被 `except Exception` 吞成 None）。
- 2026-10-28 — 修两个阻断级 bug（`version: 2026.10.28`）：(1) `_mineru_curl` 的 curl 参数缺 `-g`，源文件名含方括号（如 `[项目甲]`、`[项目乙]`）时被 curl 当作 URL glob，MinerU 上传必失败，报 `curl: (3) bad range in URL`；(2) `try_docling` 用 `**os.environ` 继承全部环境变量，`http_proxy` / `https_proxy` 指向 `socks5://` 时 docling 的 httpx 抛 `ImportError: Using SOCKS proxy, but the 'socksio' package is not installed` 快速退出，现已剔除三个代理变量（docling 本就 `HF_HUB_OFFLINE=1` 全离线运行，不该看见代理）。新增 `README.md`：支持格式与保真度对照、token 配置、排障方法、已知坑清单。
- 2026-10-17 — 读链接入 MinerU 云端：PDF/图片优先走 `mineru.net`（`try_mineru` / `mineru_extract`），失败回退本地链；新增 `--backend mineru|local`、`scripts/read_folder.py`、`references/mineru.md`；修正 timeout 默认值文档（300s→600s）、direct 读取器依赖说明、`.conversion.log` 触发条件说明。
- 2026-07-02 — macOS Apple Silicon 插入 Apple Vision OCR（扫描 PDF 快路径）。
- 2026-06-24 — docling 超时默认值 300→600s；`.doc` 经 LibreOffice 转换。
- 2026-06-22 — docling-first 降级链建立。
