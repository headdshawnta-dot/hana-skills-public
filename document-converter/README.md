# Document Converter

把任何格式的文档读成 Markdown，走一条五层降级链。任何一层失败自动落到下一层，全程留痕。

这个 skill 是所有 ingest、读文档、解析 PDF 请求的唯一入口。不要绕过它直接调 docling 或 markitdown。

---

## 降级链

```
1. MinerU 云端 VLM     所有支持的格式     保真度最高，表格结构完整、保留 colspan
   ↓ 失败 / 超 200MB / 额度用尽 / 无 token
2. Docling             本地主干           表格结构，宽表会塌成单行
   ↓ 失败
3. Apple Vision OCR    仅 PDF，仅 macOS ARM 纯文本，表格散架但文字准
   ↓ 失败
4. MarkItDown          轻量兜底            Office 够用，PDF/OCR 不行
   ↓ 失败
5. 格式直读            stdlib 兜底          .doc 走 textutil，OOXML 走 zip 解析
```

所有格式都从第 1 层进。MinerU 是一个「先试」，不是过滤器：它处理不了、额度用完、网络不通、没配 token，都会继续往下走，本地链没有变。

到本地层时：`.doc` 被 docling 与 markitdown 双双拒绝，落 direct 的 textutil；`.xls` 只有 markitdown 能读，读不了就整链失败（direct 分支不含 `.xls`）。这两种文件以前会在本地失败，现在只要 MinerU 可用就能拿到完整表格。

每一层失败都会写一个 `.conversion.log` 同伴文件，记录每次尝试的 backend / status / reason / elapsed。**整条链一次成功、没有降级时，不写 log。** 这个 log 写在输出文件旁边，不在源文件目录。

## 格式与预期

| 格式 | 实际落点 | 保真度 |
|---|---|---|
| `.pdf` | MinerU → docling → Vision → direct | 好 / 中 |
| 图片（png/jpg/webp…） | MinerU → docling（有 OCR 层，可本地兜底）→ direct 不接图片 | 好 |
| `.docx` | MinerU → docling → markitdown → direct | 好（MinerU）/ 差（宽表落 docling 时，见坑 3） |
| `.xlsx` `.xlsm` | MinerU → docling → markitdown → direct | 好（direct 有 80 行 / 30 列截断，见坑 7） |
| `.pptx` | MinerU → docling → markitdown → direct | 待实测（MinerU 路径尚未专项验证） |
| `.doc` | MinerU → direct(textutil)（中间两层不支持） | 好（MinerU）/ 差（落 textutil 时） |
| `.xls` | MinerU → markitdown（direct 不接 .xls） | 好（MinerU）/ 中 / 失败 |
| `.html` `.htm` `.epub` | docling → markitdown。**不进 MinerU**：v4 拒收 html（`-10002`），且拒绝是整批生效，一个 html 会连坐同批所有文件 | 好 |

## 安装前提

**Docling**：独立 venv，路径 `~/.workspace/.venv-docling/`。实际调用的是 venv 里的 console script `~/.workspace/.venv-docling/bin/docling`（pip 生成，入口是 `from docling.cli.main import app`）。`~/.local/bin/docling` 只是个转发 wrapper，代码不读它，装在那里没用。

不能用 `python -m docling`（docling 包没有 `__main__`），但 console script 本身能直接跑，不需要额外包 wrapper。

**MinerU**：两种方式配 token，按顺序解析：

1. 环境变量 `MINERU_TOKEN`
2. 凭证文件 `~/.hanako/secrets/mineru.token`（裸 token 单行，权限 600）

两处都没有时，第 1 层直接跳过并写 `MINERU_TOKEN not set and no credential file`，链自动落到 docling。token 注册在 mineru.net，免费额度按日计。

**Apple Vision**：macOS Apple Silicon 才有，仅处理 PDF，实现在 `bin/ocr-vision.swift`。

**LibreOffice**：`find_soffice()` / `convert_doc_to_docx()` 存在但**没有任何调用点**，`.doc` 实际走 textutil。而且这两个函数本身有缺陷，见「已知坑」第 4 条。

## 用法

```bash
# 单文件读取，走完整降级链
python3 scripts/read_document.py input.pdf \
    --backend auto \
    --output output.md \
    --format markdown \
    --docling-timeout 600

# 强制单一后端（跳过降级）
--backend mineru | docling | vision | markitdown | direct | local

# 整个目录
python3 scripts/read_folder.py INPUT_DIR --output-dir OUT [--backend auto]
```

编辑类操作绕开降级链，直接用 python-docx / openpyxl / python-pptx / pypdf：

```bash
python3 scripts/edit_docx.py in.docx out.docx --ops ops.json
python3 scripts/edit_xlsx.py in.xlsx out.xlsx --ops ops.json
python3 scripts/edit_pptx.py in.pptx out.pptx --ops ops.json
python3 scripts/edit_pdf.py  in.pdf  out.pdf  --ops ops.json
```

`--backend auto` 是默认值。`--backend local` 只跑本地链，不调 MinerU（离线或想省额度时用）。

## 自检

```bash
# token 是否就位（只看存在性，永远不阻塞）
node scripts/check_env.mjs --capability mineru-token

# 实际工具链自检：以 CLI 为准，不以 python import 为准
~/.workspace/.venv-docling/bin/docling --version
which markitdown
```

注意 `check_env.mjs --capability read-default` 的口径：它查的是**系统 python3 能否 import** `docling` / `markitdown`，而实际转换走的是 CLI（docling 在独立 venv，markitdown 在 homebrew）。在正确的环境里这两者会对不上，报 `missing_dependency` 属预期，不代表链路坏了。判断按上面两条 CLI 命令。

## 排障

先看同伴文件：

```bash
cat output.md.conversion.log
```

注意这个 log 写在**输出文件**旁边，不在源文件目录。平时如果都输出到临时目录，源目录里一本也找不到。

三行典型输出：

```
backend=mineru, status=fail, reason=MINERU_TOKEN not set and no credential file, elapsed=0.0s
backend=docling, status=ok, elapsed=17.8s
```

有 log = 发生过降级 = 保真度可能下降。**只看到第一层成功、没有 log，才是满血输出。**

判断依据不是返回码，是内容。返回码 0 不等于结果可用，见下面第 3 条。

## 已知坑

**1. 文件名含方括号，MinerU 上传必失败**（2026-10-28 已修）

`curl` 默认把参数里的 `[]` 当 URL 字符范围解析，`[项目甲]故障报告.pdf` 会被当成 glob 报 `curl: (3) bad range in URL`。中文项目文件普遍用这种命名，等于 MinerU 完全用不上。

修法：`_mineru_curl` 的 curl 参数加 `-g` 关闭 glob。

同一个方括号还会在 wiki 层破坏 wikilink：文件名带 `[...]` 时，页面标题行会被解析成嵌套爆炸形式。那是 wiki 侧的问题，与本 skill 无关，但命名习惯是同一个源头。

**2. socks5 代理让 docling 直接崩**（2026-10-28 已修）

`http_proxy` / `https_proxy` 指向 `socks5://` 时，docling 子进程里的 httpx 抛
`ImportError: Using SOCKS proxy, but the 'socksio' package is not installed`，实测 3 到 4 秒内退出。

docling 运行时本来就设了 `HF_HUB_OFFLINE=1`（模型全部本地缓存，不联网），所以它不该看见任何代理变量。修法：`try_docling` 启动子进程时剔掉 `http_proxy` / `https_proxy` / `all_proxy`，不装 socksio。

**3. 宽列表格在 docling 那层会塌成单行**（2026-10-28 起不再默认触发）

docling 处理列数多的表格时，会把整张表塌成一行、用空格填充。实测产物从 796KB 到 142MB 不等，其中单行可达 407KB。

这是 docling 自身的行为，没有改。改的是路由：现在所有格式都先走 MinerU，宽表在云端就能拿到完整结构，不会落到 docling。**只有 MinerU 不可用时**（无 token、额度耗尽、断网、超 200MB）才会重新碰到这个问题。

实测同一份 19 列质量单：MinerU 1237 字符、`colspan` 完整；docling 200113 字符、内容被截掉大半。

`read_document.py` 会按 `--max-chars` 截断（默认 200000 **字符**，中文 UTF-8 下约 600KB 字节），尾部插入 `[Truncated by read_document.py max-chars limit]`，并在 `## Reader Warnings` 里写 `content truncated by max-chars`。

判别：现在不用人工看了。`try_docling` 有质量门，单行超过 5000 字符或非空白占比低于 3% 的产物会被判为 unusable 并继续降级，log 里会出现 `docling output unusable (single line of N chars)`。**不要按「文件大小接近 200000 字节」判断**，那是 max-chars 截断后的长度，不是原始产物大小。

遇到这类文件，改用 markitdown 直读。

**4. `.doc` 的 LibreOffice 路径没接线**（import 已修，接线未做）

`convert_doc_to_docx()` 没有任何调用点，`.doc` 实际走 `read_doc_textutil()`，输出纯文本、表格结构全丢。

更麻烦的是这个函数自身缺 `import subprocess`：`office_common.py` 模块级没有导入，函数体内也没补（同文件其他用 subprocess 的函数都补了）。一旦被调用就抛 `NameError`，被自己的 `except Exception` 吞掉后静默返回 None。

所以不是「写好待接线」，是接线之前得先修这一行。

**5. `.xls` / `.doc` 不被 `obsidian-wiki batch-plan` 识别**（外部工具，但根源在两边）

用 obsidian-wiki 做批量 ingest 时，这两类老格式会被静默过滤，既不进待处理列表也不报错。判断标准不是工具报的数量，是磁盘实际文件数。

注意这不能只归给外部工具：`.xls` 在本 skill 内部也只有一个后端（markitdown）能读，`.doc` 只能落 textutil。就算 batch-plan 把文件递进来，转换质量也不高。

**6. `.docling-out-*` 残留目录**（2026-10-28 已修）

旧实现把产物目录建在 `path.parent`，也就是源文件摹旁，全仓库没有任何一处清理。实测累积 **380 个**目录、**1.0G**，单个产物最大 **142MB**。

现在的做法：产物统一落到 `tempfile.mkdtemp(prefix="docling-out-")`，在 `try_docling` 的 `finally` 块里无条件 `shutil.rmtree`。源目录不再被写入，同名文件也不再有复用陈旧产物的路径。

存量 380 个目录已一次性清掉，manifest 里指向它们产物的 20 条重复记录同步删除。

**7. direct 读 xlsx 截断，原先不给任何提示**（2026-10-28 已修）

`read_xlsx` 按 `--max-rows`（默认 80）和 `--max-cols`（默认 30）截断。旧版什么都不说，台账类文件落到 direct 兜底后，后面的行凭空消失且看起来完整。

现在会在 `## Reader Warnings` 里列出每个 sheet 的实际行数（注意：`.conversion.log` 不记录 warning，只有 Reader Warnings 里有）：

```
sheet '第15列-已完成': truncated at --max-rows=80, source has 107 row(s); raise --max-rows to see the rest
```

需要完整台账时显式加 `--max-rows 0`（或其他足够大的值）。

**8. 图片链接只在落到 direct 兜底时才断**（MinerU 路径已正常）

走 MinerU 时图片会被抽到 `<文件名>_mineru/images/`，正文里带正确路径；docx 用的 `<img>` 语法在 2026-10-28.1 已补上路径前缀。

只有链路一路失败、落到 direct 直读时才出问题：图片变成 `![embedded-image-1](base64-omitted)`，是个占位符，渲染出来是破图，质量记录单里的现场照片就这样消失。

**9. Vision 的 60 秒硬上限**（2026-10-28 已修）

旧版写死 `try_vision(path, timeout=min(timeout, 60))`，按 Vision 实测 1.2 秒/页推算，超过约 50 页的扫描件永远等不到 OCR，会一路掉到无 OCR 的兜底后整链失败。

现在上限提到 600 秒（约 500 页），仍受 `--docling-timeout` 约束。

**10. MinerU 会在输出旁边生成 `<文件名>_mineru/` 目录**（设计如此）

转换成功时，`xxx.md` 旁边会多一个 `xxx_mineru/images/`，里面是抽取出的图片。只拷 `.md` 走人，所有图片链接都会断。

docx 的图片是 `<img src="...">`，pdf/doc 是 `![](...)`，两种语法在 2026-10-28 之前只有后者被加了路径前缀，导致 docx 的图片链接一直指向不存在的 `images/`。已修。

## 变更记录

完整历史见 `SKILL.md` 末尾。当前版本 `2026.10.28.1`（2026-10-28 当日第三轮修订）。

- 2026-10-28.1（夜）— 独立审查后修正七项：`.html` 移出 `MINERU_EXTS`（MinerU 拒收且整批连坐）；`--max-rows/--max-cols 0` 改为「不限」；新增 docling 输出质量门（宽表塌成单行时判失败并继续降级）；docx 的 `<img>` 图片链接补前缀；`read_folder` 预检失败回落本地；`reader` 字段改报实际后端；空白 sheet 不再发假截断警告。文档八处陈旧表述对齐。
- 2026-10-28（晚）— 降级链入口从「PDF/图片」扩到**全格式**：`MINERU_EXTS` 加入 doc/docx/ppt/pptx/xls/xlsx（html 当晚因整批连坐被移出），MinerU 变成这些格式的第一尝试，失败再走本地链。理由是实测发现宽表 docx 在 docling 层会被静默弄坏，而 MinerU 能完整保留 `colspan`。同步更新 SKILL.md 降级链图与 `references/mineru.md` 的选型说明。
- 2026-10-28 — 修 MinerU 上传的方括号 glob bug；修 docling 的 socks5 代理崩溃；`try_docling` 改用临时目录并即时清理（清掉存量 380 个目录 / 1.0G）；direct 读 xlsx 截断时补 warning；Vision 超时上限 60→600 秒；`convert_doc_to_docx` 补 `import subprocess`。
- 2026-10-17 — 接入 MinerU 云端，PDF/图片优先走云端 VLM。
- 2026-07-02 — macOS Apple Silicon 插入 Apple Vision OCR 快路径。
- 2026-06-24 — docling 超时 300→600s。
- 2026-06-22 — docling-first 降级链建立。
