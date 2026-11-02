# hana-skills-public

Hana 平台上通用工具型 skill 的公开版本。

Skill 是纯 Markdown 的指令文件（`SKILL.md` + 参考资料与脚本），没有运行时、没有安装注册步骤——放到 agent 能读到的技能目录里就能用。

## 里面有什么

### `document-converter`

把 PDF / DOCX / XLSX / PPTX / HTML / 图片转成 Markdown，并支持对 Office 文档做定点编辑。

- PDF 与图片优先走 MinerU 云端（需要 `MINERU_TOKEN`），失败自动降级到本地链路
- 本地链路：Docling、MarkItDown、pdfplumber、pypdf、python-docx/openpyxl/python-pptx
- macOS 上额外的纯本地兜底：PDFKit + Vision 的 OCR 脚本（`bin/ocr-vision.swift`），一两秒一页
- 自带 `references/licenses.md`，列清每个依赖的许可证；刻意不引用 Anthropic 那套 source-available 的 document skills

依赖都是可选的运行时依赖，缺哪个报哪个，不会静默降级到会损坏文件的路径。

## 用法

把 skill 目录放到 agent 的技能目录下，比如：

```
<workspace>/.agents/skills/document-converter/
```

或者放到 Hana 的全局技能目录 `~/.hanako/skills/`。放好后直接说需求即可触发，不需要额外配置。

其他 skill 各自独立发布，例如 [whisper-transcribe](https://github.com/headdshawnta-dot/hana-skill-whisper-transcribe)。

## 许可证

MIT，见 [LICENSE](LICENSE)。
