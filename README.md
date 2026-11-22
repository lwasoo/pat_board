# 企业专利统计看板（单机版）

当前项目只保留本机版专利看板，不再包含 Cloudflare、D1、Drizzle、外部数据库或云端部署代码。

## 直接使用

修改源码后双击根目录 `build.bat` 离线编译。脚本只使用本地 Python、PyInstaller、PyMuPDF、OCR 引擎和模型，不运行 pip、WinGet 或网络下载，依赖缺失时明确报错。只生成 `outputs/单机版专利看板` 文件夹，不生成 ZIP、启动 BAT、说明或构建清单。直接双击其中的 `企业专利统计看板.exe`，保留必需的 `ocr` 文件夹。数据保存在 EXE 同目录的 `data/patents.db`。

本机已准备好 `.build-venv` 构建环境与中英文模型，重复构建直接复用。也可用 `build.bat -Python "本地python.exe" -TesseractExe "本地tesseract.exe"` 指定离线依赖。

## 功能

- 包含完整案件字段的 Excel 模板下载、批量导入和批量导出；费用汇总按一个专利一行，并兼容旧“缴费台账”和既有“专利家族/费用”格式
- 专利字段手动增删改查
- 专利族、月度/季度/年度统计
- 专利 PDF 本地上传、左右分栏与第二屏阅读、独权/从权区分及附图编号候选联动
- 专利下的年费/其他费用子项、全局缴费台账和临期提醒
- 企业微信群机器人测试消息及每日定时推送

## 源码

单机版源码位于 `tools/local_patent_dashboard`。先安装该目录的 `requirements.txt`，再运行 `local_dashboard.py`。扫描版 PDF 还需要安装 Tesseract 并运行 `setup_ocr.py`；完整说明见 [源码开发与 PDF/OCR 安装](tools/local_patent_dashboard/README.md)。需要重新打包时运行根目录 `build.bat`；会自动准备依赖并将 OCR 引擎、DLL 和模型一并收集到便携目录。

上传的专利 PDF 与识别结果保存在本机 `data/documents`。更换 PDF 会替换本系统保存的旧版本；删除专利会清理其上传文件，不会删除外部 PDF。备份时需保存整个 `data` 目录。
