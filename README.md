# 企业专利统计看板（单机版）

当前项目只保留本机版专利看板，不再包含 Cloudflare、D1、Drizzle、外部数据库或云端部署代码。

## 直接使用

在 Windows 上运行根目录 `build.bat` 生成完整便携包，完成后双击 `outputs/单机版专利看板/start.bat`。分发时发送生成的便携 ZIP，使用电脑完整解压后运行其中的 `start.bat`，无需另外安装 Python 或 OCR 引擎。程序仅监听 `127.0.0.1`，数据保存在 EXE 同目录的 `data/patents.db`。

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
