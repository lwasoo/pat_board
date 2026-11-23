# 企业专利统计看板（单机版）

当前项目只保留本机版专利看板，不再包含 Cloudflare、D1、Drizzle、外部数据库或云端部署代码。

## 直接使用

拉取 `trial/patent-reader` 分支后，双击根目录 `build.bat`。首次运行会校验并解压仓库 `.offline-build` 内的依赖，随后离线编译。修改 `tools/local_patent_dashboard` 中的源码后，再次双击即可重新编译，输出位于 `outputs/单机版专利看板`。

仓库附带 Windows x64 的 Python、PyInstaller、PyMuPDF、Tesseract、DLL 和中英文模型。依赖压缩包以小于 GitHub 单文件限制的分卷提交到 Git，普通 `git clone` / `git pull` 即可获得。首次拉取仓库约需额外传输 146 MB，换电脑时也可复制完整仓库离线编译。

依赖更新由 Windows 构建流程准备，校验、解压和离线编译通过后提交到当前分支。正常修改业务源码会复用已提交的依赖。

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
