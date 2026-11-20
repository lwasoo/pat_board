param(
    [string]$Python = "",
    [string]$TesseractExe = "",
    [string]$PipIndex = "",
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
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw "未找到 Tesseract，且此电脑没有 winget。请先安装 Tesseract 5，再运行本脚本。"
        }
        Write-Host "[OCR] 首次打包需要安装 Tesseract，Windows 可能要求确认安装许可或管理员权限。" -ForegroundColor Yellow
        Invoke-Checked -Executable "winget" -Arguments @("install", "--exact", "--id", "UB-Mannheim.TesseractOCR", "--version", "5.4.0.20240606", "--source", "winget")
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
