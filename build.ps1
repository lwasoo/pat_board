param(
    [string]$Python = "",
    [string]$TesseractExe = "",
    [string]$PipIndex = "",
    [string]$OcrInstaller = "",
    [switch]$NoInstallOcr,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
Set-Location -LiteralPath $PSScriptRoot
$buildExit = 0

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "命令执行失败（退出码 $LASTEXITCODE）：$Executable" }
}

try {
    if ($env:OS -ne "Windows_NT") { throw "请在 Windows 上运行 build.bat。" }
    Write-Host "企业专利统计看板 - Windows 完整便携版打包" -ForegroundColor Cyan
    if (-not $Python) {
        $projectPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
        if (Test-Path -LiteralPath $projectPython) { $Python = $projectPython }
        elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $Python = (& py -3 -c "import sys; print(sys.executable)")
            if ($LASTEXITCODE -ne 0) { throw "未找到可用的 Python 3。" }
        }
        elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $Python = (& python -c "import sys; print(sys.executable)")
            if ($LASTEXITCODE -ne 0) { throw "未找到可用的 Python 3。" }
        }
        else { throw "请先安装 Python 3.10+（64 位），或用 -Python 指定 python.exe。" }
    }
    $Python = ([string]$Python).Trim()
    Invoke-Checked -Executable $Python -Arguments @("-c", "import sys,struct; assert sys.version_info >= (3,10) and struct.calcsize('P') == 8, 'Python 3.10+ x64 required'")
    $buildPython = Join-Path $PSScriptRoot ".build-venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $buildPython)) {
        Invoke-Checked -Executable $Python -Arguments @("-m", "venv", ".build-venv")
    }
    $pipArguments = @("-m", "pip", "install", "--disable-pip-version-check", "--timeout", "120", "--retries", "5", "-r", "tools/local_patent_dashboard/build-requirements.txt")
    if ($PipIndex) { $pipArguments += @("--index-url", $PipIndex) }
    Invoke-Checked -Executable $buildPython -Arguments $pipArguments

    $findArguments = @("tools/local_patent_dashboard/build_windows.py", "--find-tesseract")
    if ($TesseractExe) { $findArguments += @("--tesseract", $TesseractExe) }
    $runtime = & $buildPython @findArguments
    $findExit = $LASTEXITCODE
    if ($findExit -eq 3) {
        if ($NoInstallOcr) { throw "未找到 Tesseract 5；请用 -TesseractExe 指定引擎路径。" }
        $cacheInstaller = Join-Path $PSScriptRoot ".build-cache\ocr\tesseract-ocr-w64-setup-5.4.0.20240606.exe"
        $installerToRun = ""
        if ($OcrInstaller) {
            $installerToRun = (Resolve-Path -LiteralPath $OcrInstaller).Path
        }
        elseif (Test-Path -LiteralPath $cacheInstaller) {
            & $buildPython "tools/local_patent_dashboard/ocr_installer.py" --verify $cacheInstaller
            if ($LASTEXITCODE -eq 0) { $installerToRun = $cacheInstaller }
        }
        if (-not $installerToRun) {
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                Write-Host "[OCR] 首次打包需要安装 Tesseract，Windows 可能要求确认安装许可或管理员权限。" -ForegroundColor Yellow
                & winget install --exact --id UB-Mannheim.TesseractOCR --version 5.4.0.20240606 --source winget
                $wingetExit = $LASTEXITCODE
                if ($wingetExit -ne 0) {
                    # Only retry transport failures. Do not restart a cancelled/rejected installation.
                    $networkErrors = @(-2147012867, -2147012894, -2147012889)
                    if ($networkErrors -notcontains $wingetExit) {
                        throw "WinGet 安装失败（退出码 $wingetExit）。可通过 -OcrInstaller 提供已下载的官方安装包。"
                    }
                    Write-Host "[OCR] WinGet 网络下载失败，改用 Python HTTPS 下载，并保留证书和 SHA-256 校验。" -ForegroundColor Yellow
                }
                $runtime = & $buildPython @findArguments
                $findExit = $LASTEXITCODE
            }
            if ($findExit -eq 3) {
                Invoke-Checked -Executable $buildPython -Arguments @("tools/local_patent_dashboard/ocr_installer.py", "--download", $cacheInstaller)
                $installerToRun = $cacheInstaller
            }
        }
        if ($installerToRun) {
            Invoke-Checked -Executable $buildPython -Arguments @("tools/local_patent_dashboard/ocr_installer.py", "--verify", $installerToRun)
            Write-Host "[OCR] 安装包校验通过，正在打开安装窗口；完成安装后继续打包。" -ForegroundColor Cyan
            $installation = Start-Process -FilePath $installerToRun -Wait -PassThru
            if ($installation.ExitCode -ne 0) { throw "OCR 安装未完成（退出码 $($installation.ExitCode)）。" }
        }
        $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = @($machinePath, $userPath, $env:Path) -join ";"
        $runtime = & $buildPython @findArguments
        $findExit = $LASTEXITCODE
    }
    if ($findExit -ne 0 -or -not $runtime) {
        throw "Tesseract 路径检查失败。请检查 PATENT_TESSERACT_CMD，或用 -TesseractExe 指定实际路径。"
    }
    $runtime = ([string]$runtime).Trim()
    Write-Host "[OCR] 将收集引擎、DLL 和模型，并验证移位后的实际识别能力。" -ForegroundColor Cyan
    Invoke-Checked -Executable $buildPython -Arguments @("tools/local_patent_dashboard/build_windows.py", "--tesseract", $runtime)
    Write-Host "打包完成。请分发完整 ZIP；使用电脑解压后双击 start.bat。" -ForegroundColor Green
}
catch {
    Write-Host ("打包失败：" + $_.Exception.Message) -ForegroundColor Red
    $buildExit = 1
}
finally {
    if (-not $NoPause) { Read-Host "按回车键退出" | Out-Null }
}
exit $buildExit
