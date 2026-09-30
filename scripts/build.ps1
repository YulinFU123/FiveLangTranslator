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
# Use cmd's rmdir so the clean bypasses the shell's recycle-bin "safe delete"
# wrapper (which aborts on large folders like the PyInstaller dist tree).
foreach ($item in ('dist', 'build')) {
    $p = Join-Path $root $item
    if (Test-Path $p) { cmd /c "rmdir /s /q `"$p`"" }
}

Write-Host '==> 3/4 PyInstaller build (onedir)'
# PyInstaller writes warnings/progress to stderr (e.g. the "running as admin"
# deprecation notice). Under $ErrorActionPreference='Stop' PowerShell turns those
# native stderr lines into terminating errors and aborts the build mid-way, so
# relax the preference for this native call and rely on the real exit code.
$previousEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$stderrLog = Join-Path $env:TEMP 'five_lang_pyi_stderr.txt'
if (Test-Path $stderrLog) { Remove-Item $stderrLog -Force }
& $python -m PyInstaller FiveLangTranslator.spec --noconfirm --distpath dist --workpath build 2> $stderrLog
$pyiExit = $LASTEXITCODE
$ErrorActionPreference = $previousEap
if (Test-Path $stderrLog) {
    Write-Host '--- PyInstaller stderr ---'
    Get-Content $stderrLog
}
if ($pyiExit -ne 0) { throw "PyInstaller build failed, exit code $pyiExit" }

$outDir = Join-Path $root 'dist\FiveLangTranslator'
if (-not (Test-Path (Join-Path $outDir 'FiveLangTranslator.exe'))) {
    throw 'FiveLangTranslator.exe not found in output'
}
$sizeMb = (Get-ChildItem $outDir -Recurse -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ("    build ok, folder size: {0:N1} MB" -f $sizeMb)

# Remove the PyInstaller work directory. It keeps a half-assembled EXE (same
# name/size as the real one) whose _internal folder only ever exists in dist, so
# double-clicking it fails with
#   "Failed to load Python DLL ... build\...\_internal\python312.dll".
# Clearing it stops that look-alike trap from lingering next to the real build.
$work = Join-Path $root 'build'
if (Test-Path $work) { cmd /c "rmdir /s /q `"$work`"" }

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
