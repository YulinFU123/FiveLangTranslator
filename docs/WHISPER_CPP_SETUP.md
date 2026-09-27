# whisper.cpp Vulkan Setup on Windows

## 1. Build prerequisites

Install:

- Git
- CMake
- Visual Studio Build Tools with Desktop C++
- Vulkan SDK
- Current AMD display driver

## 2. Build

Open a Developer PowerShell in the project root:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_whisper_cpp_vulkan.ps1
```

The script places `whisper-cli.exe` and required DLLs in:

```text
tools\whisper.cpp\
```

## 3. Model

Place a whisper.cpp-compatible GGML model in `models\`. A multilingual model is required for Chinese, English, Japanese, Russian, and German. Model files are not bundled because their licenses and redistribution terms must be tracked separately.

## 4. Check

```powershell
python .\scripts\check_whisper_backend.py
```

Then open the app's **本地识别** page and apply the executable, model, language, and GPU settings.

## Integration scope

`v0.3.0-alpha.1` invokes `whisper-cli` with 16-bit, 16 kHz mono temporary WAV files and parses JSON output. Draft segments use beam size 1, while final segments use beam size 3. This process-per-request design is intentionally simple and will later be replaced by a persistent server or native binding for lower latency.
