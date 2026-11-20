# 企业专利统计看板（单机版）

## 源码开发

在本目录执行 `python3 local_dashboard.py` 启动服务，默认监听 `127.0.0.1:8765`。
需要 Python 3.10+。台账使用标准库，PDF 解析依赖 PyMuPDF；先执行
`python -m pip install -r requirements.txt`。无桌面环境时可以用
`BROWSER=true python3 local_dashboard.py` 跳过自动打开浏览器。
`/health` 返回 `ok: true`、`mode: local` 表示服务已启动。

后端按职责拆分，模块位于启动脚本同目录，PyInstaller 仍使用原有 spec：

- `local_dashboard.py`：程序入口，供源码运行及 EXE 打包使用。
- `dashboard_config.py`：本地配置、数据与静态资源路径。
- `dashboard_validation.py`：共享字段整理与格式处理，不访问数据库或 HTTP。
- `dashboard_database.py`：SQLite 表结构、迁移和专利/费用持久化。
- `dashboard_excel.py`：Excel 解析与生成，保留现有导入规则。
- `dashboard_notifications.py`：企业微信通知及 Windows 定时任务。
- `dashboard_server.py`：台账 HTTP 路由及服务生命周期。
- `dashboard_document_routes.py`：PDF 上传、读取、重识别和校对接口。
- `dashboard_documents.py`：本地文件存储、后台 OCR、附图编号匹配。
- `dashboard_patent_text.py`：中美专利版面阅读顺序、章节边界、权利要求和 INID 字段解析。

依赖方向为：配置与字段处理 → 数据库/Excel → 通知 → HTTP 服务 → 入口。
模块使用 `dashboard_config` 中的统一路径，避免各自保存可变路径副本。
源码模式的 Windows 定时任务始终调用 `local_dashboard.py`。

在本目录执行 `python3 -m unittest discover -v` 运行测试。HTTP 回归测试使用
独立临时数据库和随机本地端口，不读写实际台账、不发送企业微信消息。
当前已知 Excel 费用国家归属的往返测试失败，结构整理未改变该业务规则。

## 使用说明

- 双击 EXE 后在本机 `127.0.0.1:8765` 启动。
- SQLite 数据库保存在 EXE 同目录的 `data` 文件夹；上传的 PDF 与识别结果保存在 `data/documents/<文件版本>/`，不发送到外部识别服务。备份时请保存整个 `data` 目录。
- 默认只允许本机访问；以后把 `local_config.json` 的 `host` 改为 `0.0.0.0`，并配置 Windows 防火墙后可扩展为公司内网版。
- “专利台账”支持国家/地区、代理编号、公开/授权/到期日、发明人、权项数量、稿件状态等字段，提供 Excel 模板、批量导入、手动增删改查，以及勾选或按筛选结果批量导出。
- Excel 导入兼容系统下载的“专利台账”模板，也兼容既有的“专利家族”工作表；合并单元格会按专利族自动继承，“申请号+公开号”和多行日期会自动拆分。
- 既有“费用”工作表会按国家和费用项目拆成逐条记录，导入“缴费管理”；金额、原始值和可识别的缴费日期都会保留。没有明确期限的历史费用不会触发临期提醒。
- 批量导出的 Excel 同时包含“专利台账”和“费用汇总”工作表；费用按一个专利一行展示。
- 月度、季度、年度统计集中在“统计分析”页；专利按族归集展示。
- “缴费管理”保存年费等费用，并在总览提醒 90 天内临期和逾期事项。
- “消息通知”可配置企业微信群机器人 Webhook；保存后由 Windows 定时任务每天检查，并在 90 天、30 天、7 天、当天和逾期阶段各推送一次。
- Webhook 仅保存在本机 `data/notification_config.json`，不会写入 Excel；自动推送要求电脑在设定时间处于开机登录状态并能访问企业微信。

## PDF 阅读与 OCR

点击台账中的专利名称或“打开专利”，进入阅读工作台，再上传 PDF。
支持最大 50 MB、150 页的未加密 PDF，每件专利保存一个当前版本。
更换文件会替换旧文件及校对结果，台账字段和费用不变。

- 左右栏可以独立查看基本信息、权利要求、说明书、附图、原 PDF 和本专利费用。
- 可拖动中间分隔条调整栏宽，“第二屏窗口”打开独立窗口，手动拖到第二块屏幕即可。
  同源窗口间通过 BroadcastChannel 同步部件选择、费用与校对结果。
- 独权和从权按引用文本区分，可分别筛选。从权展示识别出的引用编号；此分类应对照原文校对。
- 年费和其他费用分别作为专利的子项，可直接增删改；原有全局缴费台账保留作为汇总入口。
- 文字版 PDF 优先提取文字；整页扫描优先保留原始像素比例，避免不必要的插值降低文字质量；扫描页使用 Tesseract 高精度中文模型及英文 OCR，低置信度文字片段会单独提示核对。
  原 PDF 始终可读，OCR 缺失或未识别的页面会提示核对。
- 图号使用两种页面分割方式进行 OCR，并保留文字坐标；点击权利要求中的部件编号，
  另一栏高亮相同编号的候选位置。黄色虚线是候选、绿色是人工补标。
  这是编号匹配，不代表已理解附图引线或完成部件语义确认，不应当作无误的识别结论。
- 可用“校对识别文字”修改基本信息、权利要求和说明书；用“手工标注图号”在图中补标。
  这些结果不会自动覆盖台账。重新识别会替换人工校对结果，界面会先提示。
- 识别在后台逐页执行，服务中断后可“重新识别”。并发上传排队上限为 4 个文件。

### 安装 OCR

1. 安装 Tesseract 5（Linux 通常为系统包 `tesseract-ocr`；Windows 安装说明见
   <https://tesseract-ocr.github.io/tessdoc/Installation.html>）。
2. 在本目录执行 `python setup_ocr.py` 下载官方固定版本的中文和英文模型。
   脚本校验 SHA-256，并保留 TLS 校验；有效模型会复用。
3. 启动 `python local_dashboard.py`。访问 `/api/patents/document/capabilities` 可检查
   `pdf`、`ocr` 是否为 `true`，`languages` 是否包含 `chi_sim` 和 `eng`。

可通过 `PATENT_TESSERACT_CMD` 指定 Tesseract 可执行文件，或通过
`PATENT_TESSDATA_DIR` 指定已有模型目录。Windows 默认也会检查
`C:\Program Files\Tesseract-OCR\tesseract.exe`。

PyInstaller 打包前，应在同一 Python 环境安装 `requirements.txt` 并运行 `setup_ocr.py`；
spec 会收集本地 OCR 模型。Tesseract 可执行程序需另行安装，它不会自动打包进 EXE。
本轮在 Linux 上验证了源码工作流，未验证 Windows EXE。

### 验证

在本目录运行 `python -m unittest discover -v`。`test_documents.py` 使用生成的中文文字版和
扫描版 PDF 验证上传、页面分类、独权/从权、图号坐标、人工校对、文件读取和错误处理；
需要先安装上述 PDF/OCR 依赖。所有测试使用临时台账，不访问真实业务数据。

可选的浏览器回归：安装 `playwright` 与 Chromium 后执行 `python browser_smoke.py`。
该脚本使用临时数据验证台账入口、分栏、图号高亮、手工补标/撤销、第二窗口同步、
费用子项增删改、识别文字校对、PDF 更换及手机窄屏布局；不会修改业务台账。

## 解析规则 v2：中美真实 PDF 验证

PDF 自动识别章节，无需逐页手工指定类别。美国专利按左栏到底、再读右栏的顺序提取，
移除运行页眉、栏号及中缝行号；`Sheet N of M` 标识实际附图页，
`DESCRIPTION OF DRAWINGS` 属于说明书正文。`What is claimed is:` 可在页中切换章节，
同一 PDF 页可以同时包含说明书与权利要求，跨栏和跨页的权利要求会接续。
中文页面保留已有可用文字，短续页不会仅因文字少就强制 OCR。
INID 字段支持中英文标签；部件名称支持带括号和不带括号的编号。

会将提取项数、章节页数与首页声明交叉检查；不一致时明确提示核对，不会补造内容。
这些规则不是专利局结构化全文的替代品，也不能保证各种扫描质量、排版都无需校对。

升级时先退出旧服务，再更新源码并重新启动。已有 PDF 的旧识别结果会提示规则更新，
点击“重新识别”应用新解析。原 PDF、台账及费用不需要重建；已人工校对的识别文字
会在重新识别时被替换，界面会先提示。

实际验证了用户提供的 `CN119853429A`（28 页）与 `US10211630B1`（22 页）。
前者提取 19 项权利要求（1 项独权），后者提取 24 项（4 项独权），分别验证了 11 页与
7 页附图及图号定位。真实样本回归的使用方式及逐页预期见
[fixtures README](tests/fixtures/patents/README.md)。PDF 原文件不随代码提交。

### Windows EXE 出现 WinError 2

识别模型（`chi_sim.traineddata`、`eng.traineddata`）与 OCR 引擎是不同的依赖。
当前 spec 收集模型，`build.bat` 不会把 Tesseract 自动放入 EXE。
运行扫描件及附图 OCR 时需安装 Tesseract 5；默认查找 PATH 及
`C:\Program Files\Tesseract-OCR\tesseract.exe`，也支持 EXE 同目录的
`ocr/tesseract/tesseract.exe`。便携分发需要复制完整运行目录及依赖 DLL，不能只复制一个 EXE。
通过 `PATENT_TESSERACT_CMD` 指定其他安装路径时，应在设置该变量的同一终端启动看板。

程序上传 PDF 后将其保存为 `data/documents/<版本>/original.pdf`，并将该路径写入 SQLite。
上传前的中文名称、空格及括号不需要手工处理；不要在数据目录手工改名或移动文件。
若已改名，应按数据库记录恢复原路径或通过页面重新上传，保留一份外部原件。
更新后的报错会区分缺少 OCR 引擎、引擎/DLL 无法启动以及原始 PDF 路径缺失。
Windows 路径查找和缺少引擎的情况已有模拟回归测试，尚未在 Windows 实机上执行打包验证。
