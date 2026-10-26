$ErrorActionPreference = 'Stop'
. 'C:\path\to\local-test\tools\use-d-drive.ps1'
$previewRoot = 'C:\path\to\local-test\endfield-overlays-20261018'
$previewExe = Join-Path $previewRoot 'dist\EndfieldOverlayPreview\EndfieldOverlayPreview.exe'
if (-not (Test-Path -LiteralPath $previewExe -PathType Leaf)) {
    throw 'Local preview executable is missing. See docs/overlay_pages_v1.md.'
}
Start-Process -FilePath $previewExe -ArgumentList @('--state-root', (Join-Path $previewRoot 'interactive-state')) -WindowStyle Hidden
