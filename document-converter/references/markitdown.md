# MarkItDown Notes

Used as the **first fallback** after docling fails. Do not call directly unless `--backend markitdown` is forced.

## Installation

```bash
pip install "markitdown[all]"
```

`[all]` extras bundle PDF / DOCX / XLSX / PPTX / audio / outlook support.

## Extras

| Extra | Adds |
|---|---|
| `pdf` | PDF conversion (via pdfminer) |
| `docx` | DOCX conversion |
| `xlsx` | XLSX conversion |
| `pptx` | PPTX conversion |
| `audio-transcription` | Audio → text (Whisper) |
| `youtube-transcription` | YouTube URL → text |
| `az-content-understanding` | Azure Content Understanding (added in 0.1.6) |
| `outlook` | .msg files |
| `all` | Everything above |

## Known Bugs (0.1.6)

From release notes:
- **#1612** Fixed O(n) memory growth in PDF conversion by calling `page.close()` after each page.
- **#1644** Fixed RecursionError on deeply nested HTML.

If you encounter PDF memory bloat or HTML recursion crashes on 0.1.5 or earlier, upgrade to 0.1.6.

## Security Considerations

From official README:
> MarkItDown performs I/O with the privileges of the current process. Like `open()` or `requests.get()`, it will access resources that the process itself can access. Sanitize your inputs in untrusted environments, and call the narrowest `convert_*` function needed for your use case.

Do not use markitdown on untrusted URLs without sandboxing. For HTTP server mode (`docling serve` / markitdown's HTTP server), bind to `127.0.0.1` only — binding to non-local interfaces is a known security risk.
