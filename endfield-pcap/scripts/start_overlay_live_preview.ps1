param([string]$SettingsRoot = '', [switch]$OperationDiagnostics)
$ErrorActionPreference = 'Stop'
. 'C:\path\to\local-test\tools\use-d-drive.ps1'
$projectRoot = Split-Path -Parent $PSScriptRoot
$previewRoot = 'C:\path\to\local-test\endfield-overlays-20261018'
$pythonPath = Join-Path $projectRoot '.local\release_3.0.1_20260914\build-venv\Scripts\python.exe'
if (Get-Process -Name EndfieldLogsClient -ErrorAction SilentlyContinue) {
    throw 'Close the running release client before starting the local capture test.'
}
$env:PYTHONPATH = "$projectRoot\src;$projectRoot\packages\parser_core;$(Split-Path -Parent $projectRoot)\endfield-logs\packages\uploader_core"
$env:ENDFIELD_OVERLAY_SETTINGS_ROOT = if ($SettingsRoot) {
    [IO.Path]::GetFullPath($SettingsRoot)
} else { Join-Path $previewRoot 'live-windows' }
$env:ENDFIELD_LOGS_DISABLE_UPLOADER = '1'
if ($OperationDiagnostics) { $env:ENDFIELD_OVERLAY_OPERATION_DIAGNOSTICS = '1' }
else { Remove-Item Env:ENDFIELD_OVERLAY_OPERATION_DIAGNOSTICS -ErrorAction SilentlyContinue }
Set-Location -LiteralPath $projectRoot
$resourceCheck = Join-Path (Split-Path -Parent $projectRoot) 'endfield-logs\infra\scripts\game_resource_snapshot.py'
& $pythonPath $resourceCheck verify --project-root (Split-Path -Parent $projectRoot)
if ($LASTEXITCODE -ne 0) {
    throw 'Game resources changed. Refresh the selected official resources before the live overlay test.'
}
& $pythonPath -m endfield_pcap serve --log-dir (Join-Path $previewRoot 'capture') --debug-dir (Join-Path $previewRoot 'debug')
exit $LASTEXITCODE
