# FiveLangTranslator one-click build script
# Output: dist/FiveLangTranslator  (onedir portable build)
#         dist/FiveLangTranslator-<version>-win64.zip
param(
    [switch]$SkipZip
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

# Prefer the project venv, which carries PySide6 and the other runtime deps.
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Warning '.venv not found, falling back to system python (needs PySide6 installed)'
    $python = 'python'
}

Write-Host '==> 1/4 generate icon and version info'
& $python (Join-Path $root 'scripts\make_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'icon generation failed' }
& $python (Join-Path $root 'scripts\make_version_info.py')
if ($LASTEXITCODE -ne 0) { throw 'version info generation failed' }

Write-Host '==> 2/4 clean previous output'
Remove-Item -Recurse -Force (Join-Path $root 'dist'), (Join-Path $root 'build') -ErrorAction SilentlyContinue

Write-Host '==> 3/4 PyInstaller build (onedir)'
& $python -m PyInstaller FiveLangTranslator.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed, exit code $LASTEXITCODE" }

$outDir = Join-Path $root 'dist\FiveLangTranslator'
if (-not (Test-Path (Join-Path $outDir 'FiveLangTranslator.exe'))) {
    throw 'FiveLangTranslator.exe not found in output'
}
$sizeMb = (Get-ChildItem $outDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("    build ok, folder size: {0:N1} MB" -f $sizeMb)

if ($SkipZip) {
    Write-Host '    zip skipped (-SkipZip)'
    Write-Host "==> done: $outDir"
    exit 0
}

Write-Host '==> 4/4 package zip'
$version = '0.0.0'
$match = Select-String -Path (Join-Path $root 'pyproject.toml') -Pattern '^version\s*=\s*"([^"]+)"'
if ($match) { $version = $match.Matches[0].Groups[1].Value }
$zip = Join-Path $root "dist\FiveLangTranslator-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $outDir '*') -DestinationPath $zip

$zipMb = (Get-Item $zip).Length / 1MB
Write-Host ("    packaged: {0} ({1:N1} MB)" -f $zip, $zipMb)
Write-Host '==> done'
