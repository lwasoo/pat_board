# 企业专利统计看板（单机版）

当前项目只保留本机版专利看板，不再包含 Cloudflare、D1、Drizzle、外部数据库或云端部署代码。

## 直接使用

下载包含源码与编译依赖的 `patent-dashboard-source-offline-windows-x64.zip`，完整解压后修改 `tools/local_patent_dashboard` 内的代码，再双击根目录 `build.bat` 编译。输出位于 `outputs/单机版专利看板`。

源码依赖包包含 `.build-python` 下的 Python、PyInstaller、PyMuPDF，以及 `tools/local_patent_dashboard/ocr` 下的 Tesseract、DLL 和中英文模型。换到另一台 Windows 10/11 x64 电脑时复制整个目录即可离线编译。

包由 [GitHub Actions](https://github.com/lwasoo/pat_board/actions/workflows/windows-portable.yml?query=branch%3Atrial%2Fpatent-reader) 自动生成，在最近一次成功运行的 Artifacts 中下载。GitHub 自带的 Download ZIP 仅包含 Git 跟踪的源文件，编译依赖在上述源码依赖包中。

## 功能

- 包含完整案件字段的 Excel 模板下载、批量导入和批量导出；费用汇总按一个专利一行，并兼容旧“缴费台账”和既有“专利家族/费用”格式
- 专利字段手动增删改查
- 专利族、月度/季度/年度统计
- 专利 PDF 本地上传、左右分栏与第二屏阅读、独权/从权区分及附图编号候选联动
- 专利下的年费/其他费用子项、全局缴费台账和临期提醒
- 企业微信群机器人测试消息及每日定时推送

## 源码

单机版源码位于 `tools/local_patent_dashboard`。自行修改源码时，使用源码依赖包内的 `build.bat` 离线编译；完整说明见 [源码开发与 PDF/OCR 安装](tools/local_patent_dashboard/README.md)。

上传的专利 PDF 与识别结果保存在本机 `data/documents`。更换 PDF 会替换本系统保存的旧版本；删除专利会清理其上传文件，不会删除外部 PDF。备份时需保存整个 `data` 目录。
