param([string]$Python = "", [string]$TesseractExe = "", [switch]$NoPause)
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PIP_NO_INDEX = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
Set-Location -LiteralPath $PSScriptRoot
$buildExit = 0
function Show-PortableDownload {
    $url = "https://github.com/lwasoo/pat_board/actions/workflows/windows-portable.yml?query=branch%3Atrial%2Fpatent-reader"
    Write-Host "本机缺少源码编译依赖。直接使用已打包的 Windows 便携版即可。" -ForegroundColor Yellow
    Write-Host "登录 GitHub，选择最近一次成功的运行，在 Artifacts 下载 patent-dashboard-windows-x64.zip。"
    Write-Host "完整解压 ZIP，双击其中的 start.bat 或看板 EXE。便携包已经包含运行依赖。"
    Write-Host $url
    try { Start-Process $url } catch { Write-Host "请复制上方地址到浏览器打开。" }
}
try {
    if ($env:OS -ne "Windows_NT") { throw "请在 Windows 上运行 build.bat。" }
    Write-Host "企业专利统计看板 - 源码离线编译（需要本地构建环境）" -ForegroundColor Cyan
    if (-not $Python) {
        foreach ($candidate in @(".build-venv\Scripts\python.exe", ".venv\Scripts\python.exe")) {
            $path = Join-Path $PSScriptRoot $candidate
            if (Test-Path -LiteralPath $path) { $Python = $path; break }
        }
        if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) { $Python = (Get-Command python).Source }
    }
    if (-not $Python) {
        Show-PortableDownload
        throw "当前电脑无法编译源码，请使用上方的便携包。"
    }
    & $Python -c "import sys,struct,importlib.util; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 and all(importlib.util.find_spec(name) for name in ('PyInstaller','pymupdf')) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Show-PortableDownload
        throw "当前 Python 缺少编译依赖，请使用上方的便携包。"
    }
    $outputExe = Join-Path $PSScriptRoot "outputs\单机版专利看板\企业专利统计看板.exe"
    $running = @(Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $outputExe })
    if ($running.Count) {
        Write-Host "正在关闭当前输出目录的旧看板，以便替换程序。" -ForegroundColor Yellow
        $configPath = Join-Path (Split-Path $outputExe) 'local_config.json'
        $port = 8765
        if (Test-Path -LiteralPath $configPath) { $cfg = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json; if ($cfg.port) { $port = [int]$cfg.port } }
        try { Invoke-WebRequest -UseBasicParsing -Method Post -Uri "http://127.0.0.1:$port/api/shutdown" -TimeoutSec 5 | Out-Null } catch { Write-Host "旧服务未响应，将结束当前输出目录中的残留进程。" }
        Start-Sleep -Seconds 2
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $outputExe } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 1
    }
    $arguments = @("tools/local_patent_dashboard/build_windows.py")
    if ($TesseractExe) { $arguments += @("--tesseract", $TesseractExe) }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "离线构建失败，请查看上方错误信息。" }
    Write-Host "编译完成。打开 outputs\单机版专利看板，直接双击 EXE；保留 ocr 文件夹。" -ForegroundColor Green
} catch {
    Write-Host ("打包失败：" + $_.Exception.Message) -ForegroundColor Red
    $buildExit = 1
} finally {
    if (-not $NoPause) { Read-Host "按回车键退出" | Out-Null }
}
exit $buildExit
