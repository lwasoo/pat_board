# 设置编码
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location -Path $PSScriptRoot

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  企业专利统计看板 - 自动打包脚本" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "[错误] 未找到 pyinstaller，请先安装：pip install pyinstaller" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

$specPath = Get-ChildItem -Path ".\tools\local_patent_dashboard\*.spec" | Select-Object -First 1
if (-not $specPath) {
    Write-Host "[错误] 未在 .\tools\local_patent_dashboard\ 找到 .spec 文件" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

Write-Host "[OCR] 本脚本会收集已下载的识别模型，不会打包 Tesseract OCR 引擎。" -ForegroundColor Yellow
Write-Host "      使用扫描件或图号 OCR 的电脑需安装 Tesseract 5；或在 EXE 同目录保留完整的 ocr\tesseract 运行目录。" -ForegroundColor Yellow
Write-Host "      可以通过 PATENT_TESSERACT_CMD 指定 tesseract.exe。" -ForegroundColor Yellow
Write-Host ""

$outDir = ".\outputs\单机版专利看板"

Write-Host "[1/2] 正在打包，请稍候..." -ForegroundColor Yellow
Write-Host "输出目录：$outDir"
Write-Host ""

& pyinstaller --noconfirm --clean --distpath $outDir --workpath ".\.package-build" $specPath.FullName

if ($LASTEXITCODE -ne 0) {
    Write-Host "[错误] 打包失败，请检查上方日志。" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

Write-Host ""
Write-Host "[2/2] 清理临时构建目录 .package-build ..." -ForegroundColor Yellow
if (Test-Path ".\.package-build") {
    try {
        Remove-Item -Path ".\.package-build" -Recurse -Force -ErrorAction Stop
        Write-Host "    已删除 .package-build"
    } catch {
        Write-Host "    [警告] 删除失败，可能有进程占用：$($_.Exception.Message)" -ForegroundColor Yellow
    }
} else {
    Write-Host "    .package-build 不存在，跳过"
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  打包完成！输出目录：$outDir" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Read-Host "按回车键退出"