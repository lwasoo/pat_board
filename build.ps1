param([string]$Python = "", [string]$TesseractExe = "", [switch]$NoPause)
$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PIP_NO_INDEX = "1"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
Set-Location -LiteralPath $PSScriptRoot
$buildExit = 0
try {
    if ($env:OS -ne "Windows_NT") { throw "请在 Windows 上运行 build.bat。" }
    Write-Host "企业专利统计看板 - 离线打包（不联网、不下载、不安装）" -ForegroundColor Cyan
    if (-not $Python) {
        foreach ($candidate in @(".build-venv\Scripts\python.exe", ".venv\Scripts\python.exe")) {
            $path = Join-Path $PSScriptRoot $candidate
            if (Test-Path -LiteralPath $path) { $Python = $path; break }
        }
        if (-not $Python -and (Get-Command python -ErrorAction SilentlyContinue)) { $Python = (Get-Command python).Source }
    }
    if (-not $Python) { throw "缺少本地 Python 构建环境，请用 -Python 指定已准备好的环境；脚本不会下载。" }
    & $Python -c "import sys,struct; assert sys.version_info >= (3,10) and struct.calcsize('P') == 8; import PyInstaller,pymupdf"
    if ($LASTEXITCODE -ne 0) { throw "缺少本地 Python 3.10+ x64、PyInstaller 或 PyMuPDF。请补齐离线环境，未尝试联网。" }
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
