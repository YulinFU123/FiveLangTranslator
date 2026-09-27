param(
    [string]$SourceDirectory = "$PSScriptRoot\..\tools\whisper.cpp-src",
    [string]$InstallDirectory = "$PSScriptRoot\..\tools\whisper.cpp"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git not found" }
if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) { throw "CMake not found" }
if (-not $env:VULKAN_SDK) { throw "VULKAN_SDK is not set. Install the Vulkan SDK first." }

if (-not (Test-Path $SourceDirectory)) {
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git $SourceDirectory
}

Push-Location $SourceDirectory
try {
    cmake -S . -B build-vulkan -DGGML_VULKAN=ON -DGGML_CUDA=OFF -DGGML_HIP=OFF -DCMAKE_BUILD_TYPE=Release
    cmake --build build-vulkan --config Release --parallel
    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    $binary = Get-ChildItem -Path build-vulkan -Recurse -Filter whisper-cli.exe | Select-Object -First 1
    if (-not $binary) { throw "whisper-cli.exe was not produced" }
    Copy-Item $binary.FullName "$InstallDirectory\whisper-cli.exe" -Force
    $server = Get-ChildItem -Path build-vulkan -Recurse -Filter whisper-server.exe | Select-Object -First 1
    if ($server) {
        Copy-Item $server.FullName "$InstallDirectory\whisper-server.exe" -Force
    } else {
        Write-Warning "whisper-server.exe was not produced; CLI fallback remains available"
    }
    Get-ChildItem $binary.Directory.FullName -Filter "*.dll" | Copy-Item -Destination $InstallDirectory -Force
    Write-Host "Installed whisper.cpp to $InstallDirectory" -ForegroundColor Green
}
finally {
    Pop-Location
}
