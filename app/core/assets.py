from __future__ import annotations

import argparse
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core import paths


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    url: str
    size_mb: int
    note: str


@dataclass(frozen=True)
class AssetStatus:
    models_dir: str
    tools_dir: str
    installed_models: tuple[str, ...]
    whisper_server: str
    whisper_cli: str

    @property
    def ready(self) -> bool:
        return bool(self.installed_models) and bool(self.whisper_server or self.whisper_cli)


MODELS: tuple[ModelSpec, ...] = (
    ModelSpec(
        "small", "ggml-small.bin",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin",
        466, "CPU 友好，延迟低，适合大多数场景",
    ),
    ModelSpec(
        "medium", "ggml-medium.bin",
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-medium.bin",
        1533, "准确率更高，建议 GPU 或 16GB+ 内存",
    ),
)

# whisper.cpp 官方预编译 Windows 二进制（含 whisper-cli.exe / whisper-server.exe）
WHISPER_CPP_ZIP_URL = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.7.5/whisper-bin-x64.zip"
)

# Silero VAD 官方 ONNX 模型（约 2MB，由 onnxruntime 推理，避免引入 torch）
SILERO_VAD_FILENAME = "silero_vad.onnx"
SILERO_VAD_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)


def silero_vad_path() -> str:
    """Locate silero_vad.onnx: user data dir first, then the bundled copy."""
    candidate = paths.models_dir() / SILERO_VAD_FILENAME
    if candidate.is_file():
        return str(candidate)
    bundled = Path(__file__).resolve().parents[1] / "audio" / "data" / SILERO_VAD_FILENAME
    if bundled.is_file():
        return str(bundled)
    return ""


def download_silero_vad(progress=None) -> str:
    return str(download(SILERO_VAD_URL, paths.models_dir() / SILERO_VAD_FILENAME, progress))


def model_spec(key: str) -> ModelSpec | None:
    return next((m for m in MODELS if m.key == key), None)


def _find_exe(directory: Path, names: tuple[str, ...]) -> str:
    if not directory.is_dir():
        return ""
    for name in names:
        direct = directory / name
        if direct.is_file():
            return str(direct)
    for path in directory.rglob("*"):
        if path.is_file() and path.name in names:
            return str(path)
    return ""


def status() -> AssetStatus:
    """Report which models and whisper.cpp binaries are already present."""
    models_dir = paths.models_dir()
    tools_dir = paths.tools_dir()
    installed = tuple(m.key for m in MODELS if (models_dir / m.filename).is_file())
    return AssetStatus(
        models_dir=str(models_dir),
        tools_dir=str(tools_dir),
        installed_models=installed,
        whisper_server=_find_exe(tools_dir, ("whisper-server.exe",)),
        whisper_cli=_find_exe(tools_dir, ("whisper-cli.exe", "main.exe")),
    )


def download(url: str, destination: Path, progress=None, timeout: float = 60.0) -> Path:
    """Stream url to destination atomically (.part then rename)."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            written = 0
            with open(temporary, "wb") as handle:
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
                    if progress is not None:
                        progress(written, total)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    temporary.replace(destination)
    return destination


def download_model(key: str, progress=None) -> str:
    spec = model_spec(key)
    if spec is None:
        raise ValueError(f"未知模型规格: {key}（可选：{', '.join(m.key for m in MODELS)}）")
    return str(download(spec.url, paths.models_dir() / spec.filename, progress))


def download_whisper_cpp(progress=None) -> str:
    """Fetch and unpack the official whisper.cpp Windows binaries."""
    target = paths.tools_dir()
    archive = Path(tempfile.gettempdir()) / "flt-whisper-bin.zip"
    download(WHISPER_CPP_ZIP_URL, archive, progress)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(target)
    archive.unlink(missing_ok=True)
    return str(target)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="FiveLangTranslator 资源下载器")
    parser.add_argument("--model", choices=[m.key for m in MODELS], help="下载指定规格的 GGML 模型")
    parser.add_argument("--whisper", action="store_true", help="下载 whisper.cpp Windows 二进制")
    parser.add_argument("--vad", action="store_true", help="下载 Silero VAD ONNX 模型（约 2MB）")
    parser.add_argument("--status", action="store_true", help="仅显示当前资源状态")
    args = parser.parse_args()

    if args.status or not (args.model or args.whisper or args.vad):
        current = status()
        print(f"模型目录    : {current.models_dir}")
        print(f"二进制目录  : {current.tools_dir}")
        print(f"已装模型    : {', '.join(current.installed_models) or '无'}")
        print(f"server      : {current.whisper_server or '无'}")
        print(f"cli         : {current.whisper_cli or '无'}")
        print(f"vad         : {silero_vad_path() or '无'}")
        print(f"就绪        : {'是' if current.ready else '否'}")
        return 0

    def progress(written: int, total: int) -> None:
        if total:
            print(f"\r  已下载 {written / 1e6:.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        else:
            print(f"\r  已下载 {written / 1e6:.1f} MB", end="", flush=True)

    if args.model:
        print(f"下载模型 {args.model} ...")
        print("完成:", download_model(args.model, progress))
    if args.whisper:
        print("\n下载 whisper.cpp 二进制 ...")
        print("完成:", download_whisper_cpp(progress))
    if args.vad:
        print("\n下载 Silero VAD 模型 ...")
        print("完成:", download_silero_vad(progress))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
