# Environment Preflight Capabilities

Run before invoking conversion or editing:

```bash
node scripts/check_env.mjs --capability <name>
```

## Capability table

| Capability | What it checks | Used by |
|---|---|---|
| `read-default` | At least one of docling / markitdown present | Default conversion path |
| `docling-check` | docling + venv available, all ML deps | Force `--backend docling` |
| `read-fallback` | markitdown present | Force `--backend markitdown` |
| `mineru-token` | `MINERU_TOKEN` env var or `~/.hanako/secrets/mineru.token` present (**hint only, never blocks**) | PDF/image conversion via MinerU cloud |
| `edit-docx-basic` | python-docx present | DOCX `replace_text` |
| `edit-docx-rich` | python-docx present | DOCX `append_paragraph`, `add_table` |
| `edit-xlsx` | openpyxl present | All XLSX edits |
| `edit-pptx-basic` | python-pptx present | PPTX `replace_text` |
| `edit-pptx-rich` | python-pptx present | PPTX shape targeting, textbox |
| `edit-pdf` | pypdf present | PDF structural edits |

## Behavior

- Returns `{ ok: true }` if all required packages are importable.
- Returns `{ ok: false, message: <...>, installGuidance: <...> }` otherwise.
- `mineru-token` is a **non-blocking hint**: it never sets `ok:false`. Its presence is reported under `envHints` (`envPresent` / `filePresent` / `ready`). A missing token just means PDF/image conversion falls back to the local chain.
- The preflight itself uses Node built-ins only. No Python dependencies to check the preflight.
- Finds Python via `HANA_OFFICE_PYTHON`, `python3`, `python`, or Windows `py -3`.
- Requires Python 3.10+.

## When preflight fails

Do **not** auto-install dependencies. Show the user the `message` and `installGuidance`. Let them decide.
