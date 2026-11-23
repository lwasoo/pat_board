# Restore build dependencies from Git-tracked ZIP parts without Python or network.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$vendor = Join-Path $root '.offline-build'
$manifest = Get-Content -LiteralPath (Join-Path $vendor 'manifest.json') -Raw | ConvertFrom-Json
$marker = Join-Path $root '.build-python\.deps-sha256'
$targets = @('.build-python', 'tools\local_patent_dashboard\ocr\tesseract', 'tools\local_patent_dashboard\ocr\tessdata')
if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $manifest.sha256)) {
    $complete = $true
    foreach ($target in $targets) { if (-not (Test-Path -LiteralPath (Join-Path $root $target))) { $complete = $false } }
    if ($complete) { return }
}
Write-Host '正在校验并解压仓库内的编译依赖，首次运行需要等待片刻。' -ForegroundColor Cyan
$stage = Join-Path $root ('.build-cache\restore-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$archive = Join-Path $stage 'dependencies.zip'
$unpacked = Join-Path $stage 'unpacked'
$backups = @()
$installed = @()
try {
    $stream = [IO.File]::Create($archive)
    try {
        foreach ($part in $manifest.parts) {
            if ($part.name -notmatch '^dependencies\.zip\.[0-9]{3}$') { throw '依赖分卷名称无效。' }
            $path = Join-Path $vendor $part.name
            if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $part.sha256) {
                throw "依赖分卷损坏，请重新拉取：$($part.name)"
            }
            $inputStream = [IO.File]::OpenRead($path)
            try { $inputStream.CopyTo($stream) } finally { $inputStream.Dispose() }
        }
    } finally { $stream.Dispose() }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) { throw '完整依赖包校验失败。' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::ExtractToDirectory($archive, $unpacked)
    foreach ($target in $targets) {
        $source = Join-Path $unpacked $target
        if (-not (Test-Path -LiteralPath $source -PathType Container)) { throw "依赖包缺少目录：$target" }
    }
    try {
        foreach ($target in $targets) {
            $destination = Join-Path $root $target
            if (Test-Path -LiteralPath $destination) {
                $saved = Join-Path $stage ('backup-' + $backups.Count)
                Move-Item -LiteralPath $destination -Destination $saved
                $backups += ,@($saved, $destination)
            }
            New-Item -ItemType Directory -Path (Split-Path $destination) -Force | Out-Null
            Move-Item -LiteralPath (Join-Path $unpacked $target) -Destination $destination
            $installed += $destination
        }
        [IO.File]::WriteAllText($marker, $manifest.sha256)
    } catch {
        foreach ($destination in $installed) { Remove-Item -LiteralPath $destination -Recurse -Force }
        foreach ($backup in $backups) { Move-Item -LiteralPath $backup[0] -Destination $backup[1] }
        throw
    }
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host '仓库内的编译依赖已就绪。' -ForegroundColor Green
