# 企业专利统计看板（单机版）

当前项目只保留本机版专利看板，不再包含 Cloudflare、D1、Drizzle、外部数据库或云端部署代码。

## 直接使用

在 [GitHub Actions](https://github.com/lwasoo/pat_board/actions/workflows/windows-portable.yml?query=branch%3Atrial%2Fpatent-reader) 打开最近一次成功的 Windows 构建，登录后从 Artifacts 下载 `patent-dashboard-windows-x64.zip`。完整解压后双击 `start.bat` 或 `企业专利统计看板.exe`。ZIP 已包含 Python 运行时、PyMuPDF、Tesseract、DLL 和中英文模型，PDF 读取和 OCR 可离线使用。

换电脑时可以用 U 盘复制同一份 ZIP，或直接复制完整便携目录。台账和已上传的 PDF 在便携目录的 `data` 中，迁移现有台账时一并复制。

## 功能

- 包含完整案件字段的 Excel 模板下载、批量导入和批量导出；费用汇总按一个专利一行，并兼容旧“缴费台账”和既有“专利家族/费用”格式
- 专利字段手动增删改查
- 专利族、月度/季度/年度统计
- 专利 PDF 本地上传、左右分栏与第二屏阅读、独权/从权区分及附图编号候选联动
- 专利下的年费/其他费用子项、全局缴费台账和临期提醒
- 企业微信群机器人测试消息及每日定时推送

## 源码

单机版源码位于 `tools/local_patent_dashboard`。试用或分发请使用 GitHub 构建的便携包。自行修改源码时，可在已准备本地依赖的 Windows 电脑用 `build.bat` 离线编译；完整说明见 [源码开发与 PDF/OCR 安装](tools/local_patent_dashboard/README.md)。

上传的专利 PDF 与识别结果保存在本机 `data/documents`。更换 PDF 会替换本系统保存的旧版本；删除专利会清理其上传文件，不会删除外部 PDF。备份时需保存整个 `data` 目录。
