param(
    [string]$Python = 'C:\path\to\endfield-suite\endfield-pcap\.local\release_3.0.1_20260914\build-venv\Scripts\python.exe',
    [string]$TaskRoot = 'C:\path\to\local-test\endfield-overlays-20261018',
    [switch]$Validate
)
$ErrorActionPreference = 'Stop'
. 'C:\path\to\local-test\tools\use-d-drive.ps1'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskRootPath = [IO.Path]::GetFullPath($TaskRoot).TrimEnd('\')
if (-not $taskRootPath.StartsWith('C:\path\to\local-test\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Preview build output must stay in a task directory under C:\path\to\local-test.'
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Selected build Python is missing.' }
$runtime = (& $Python -c 'import json,sys; print(json.dumps(dict(base=sys.base_prefix,python=sys.executable,version=sys.version.split()[0])))') | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Selected build Python did not start.' }

# PyInstaller must not resolve DLLs from unrelated native-tool PATH entries.
# Qt 6.11 imports the Windows ICU API (ucnv_open without a version suffix).
# Poppler's same-named ICU DLL exports ucnv_open_78 and breaks frozen QtCore.
# Keep this in sync with pack_release.py's clean dependency environment.
$windowsRoot = [IO.Path]::GetFullPath($env:SystemRoot)
$env:PATH = @(
    (Split-Path -Parent $runtime.python), $runtime.base,
    (Join-Path $runtime.base 'DLLs'), (Join-Path $windowsRoot 'System32'),
    $windowsRoot, (Join-Path $windowsRoot 'System32\Wbem')
) -join ';'
Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
$env:PYTHONNOUSERSITE = '1'
$env:PYINSTALLER_CONFIG_DIR = 'C:\path\to\local-test\cache\pyinstaller'
$env:QTWEBENGINE_CHROMIUM_FLAGS = '--disable-gpu'

$null = New-Item -ItemType Directory -Path $taskRootPath -Force
$buildArgs = @(
    '-m', 'PyInstaller', '--noconfirm', '--clean', '--noupx', '--noconsole',
    '--name', 'EndfieldOverlayPreview', '--onedir',
    '--distpath', (Join-Path $taskRootPath 'dist'),
    '--workpath', (Join-Path $taskRootPath 'build'),
    '--specpath', (Join-Path $taskRootPath 'spec'),
    '--paths', (Join-Path $projectRoot 'src'),
    '--add-data', ((Join-Path $projectRoot 'src\endfield_pcap\overlay_examples') + ';endfield_pcap/overlay_examples'),
    '--add-data', ((Join-Path $projectRoot 'data\overlay') + ';data/overlay'),
    '--add-data', ((Join-Path $projectRoot 'data\game_resource_snapshot.json') + ';data'),
    (Join-Path $projectRoot 'overlay_preview_launcher.py')
)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$buildLog = Join-Path $taskRootPath "build-preview-clean-$stamp.log"
Push-Location $projectRoot
try {
    # Windows PowerShell 5 treats native stderr as error records, including
    # PyInstaller's normal INFO progress. Judge its actual process exit code.
    $priorErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Python @buildArgs 2>&1 | Tee-Object -FilePath $buildLog
        $buildExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $priorErrorPreference }
    if ($buildExitCode -ne 0) { throw "Preview build failed. See $buildLog" }
} finally { Pop-Location }

$previewExe = Join-Path $taskRootPath 'dist\EndfieldOverlayPreview\EndfieldOverlayPreview.exe'
if (-not (Test-Path -LiteralPath $previewExe -PathType Leaf)) { throw 'Preview executable was not produced.' }
Write-Output "Preview executable: $previewExe"
Write-Output "Build Python: $($runtime.python) ($($runtime.version))"
if ($Validate) {
    $resultPath = Join-Path $taskRootPath "preview-validation-clean-$stamp.json"
    $stateRoot = Join-Path $taskRootPath "preview-validation-clean-$stamp"
    $env:QT_QPA_PLATFORM = 'offscreen'
    $process = Start-Process -FilePath $previewExe -ArgumentList @('--validate', '--state-root', $stateRoot, '--result', $resultPath) -WindowStyle Hidden -Wait -PassThru
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        throw "Frozen preview validation failed. See $resultPath"
    }
    Get-Content -LiteralPath $resultPath
}
