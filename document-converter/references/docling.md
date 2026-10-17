# Docling Notes

## Installation

Hand-rolled venv at `~/.workspace/.venv-docling/`, Python 3.11.15, bash wrapper at `~/.local/bin/docling`.

Re-install if torch or docling-core gets stale:

```bash
~/.workspace/.venv-docling/bin/python -m pip install --upgrade docling
```

## OCR Engines

Docling supports multiple OCR engines. Choose via `PdfPipelineOptions`:

| Engine | Best for | Install |
|---|---|---|
| ocrmac | macOS native | Built-in (no install) |
| easyocr | Cross-platform | `pip install "docling[easyocr]"` |
| rapidocr | Lightweight, ONNX | `pip install "docling[rapidocr]"` |
| tesseract | Most configurable | `brew install tesseract leptonica pkg-config` + `TESSDATA_PREFIX=/opt/homebrew/share/tessdata/` |

On macOS arm64, **ocrmac is the default and fastest**.

## Model Selection

Docling auto-downloads layout + table structure models on first use (HuggingFace). Models cached at `~/.cache/huggingface/`.

To force CPU-only mode:

```python
from docling.datamodel.pipeline_options import PdfPipelineOptions
opts = PdfPipelineOptions()
opts.accelerator_options.device = "cpu"
```

## Platform Notes

- **macOS arm64**: works out of the box, GPU via MPS.
- **macOS Intel (x86_64)**: PyTorch 2.6.0+ doesn't ship Intel wheels. Pin `torch==2.2.2 torchvision==0.17.2`.
- **Linux CPU-only**: install via `--extra-index-url https://download.pytorch.org/whl/cpu`.
- **Linux + CUDA**: requires CUDA 13 wheels for Nemotron OCR.

## Performance

- Plain text PDF: ~0.5-2s per page.
- Complex PDF (tables, formulas): ~2-5s per page.
- Scanned PDF + OCR: ~5-15s per page.
- Large documents (>100 pages): consider splitting or accepting multi-minute runtime.

Default timeout in the conversion pipeline: **300s**. Hard cap: 7200s.

## Known Limitations

- Encrypted PDFs: unsupported.
- Form fields: extracted as text but layout may not match original.
- Math equations: recognized but LaTeX output may need cleanup.
- Tables spanning multiple pages: may merge incorrectly.
