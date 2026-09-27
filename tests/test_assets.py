import zipfile

import pytest

from app.core import assets, paths


def test_model_spec_lookup():
    assert assets.model_spec("small").filename == "ggml-small.bin"
    assert assets.model_spec("nope") is None


def test_status_reports_missing_assets(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    current = assets.status()
    assert current.installed_models == ()
    assert current.whisper_server == ""
    assert current.ready is False


def test_status_detects_installed_model(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    (paths.models_dir() / "ggml-small.bin").write_bytes(b"x")
    current = assets.status()
    assert current.installed_models == ("small",)
    assert current.ready is False  # still needs whisper binary


def test_download_writes_file_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    source = tmp_path / "payload.bin"
    source.write_bytes(b"model-bytes" * 100)

    seen = []
    destination = paths.models_dir() / "ggml-small.bin"
    result = assets.download(source.as_uri(), destination,
                             progress=lambda written, total: seen.append(written))

    assert result == destination
    assert destination.read_bytes() == source.read_bytes()
    assert seen and seen[-1] == len(source.read_bytes())
    # no leftover .part artifact
    assert not destination.with_name(destination.name + ".part").exists()


def test_download_cleans_up_partial_file_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    destination = paths.models_dir() / "ggml-small.bin"
    try:
        assets.download((tmp_path / "missing.bin").as_uri(), destination)
    except Exception:
        pass
    assert not destination.exists()
    assert not destination.with_name(destination.name + ".part").exists()


def test_download_whisper_cpp_extracts_archive(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    archive = tmp_path / "whisper-bin.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("whisper-cli.exe", b"cli")
        zf.writestr("whisper-server.exe", b"server")

    monkeypatch.setattr(assets, "WHISPER_CPP_ZIP_URL", archive.as_uri())
    assets.download_whisper_cpp()

    current = assets.status()
    assert current.whisper_cli.endswith("whisper-cli.exe")
    assert current.whisper_server.endswith("whisper-server.exe")


def test_record_hash_and_verify_model(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    source = tmp_path / "payload.bin"
    source.write_bytes(b"model-bytes" * 100)
    destination = paths.models_dir() / "ggml-small.bin"
    assets.download(source.as_uri(), destination)

    # download() 是通用下载；摘要记录发生在 download_model() 之后
    assert assets.recorded_hash(destination) == ""
    assets.record_hash(destination)
    assert assets.recorded_hash(destination)
    assert assets.verify_model("small") is True

    # 文件被篡改 -> 校验失败并删除损坏文件
    destination.write_bytes(b"corrupted")
    assert assets.verify_model("small") is False
    assert not destination.exists()


def test_download_retries_until_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    calls = []

    def failing(url, dest, progress=None, timeout=60.0):
        calls.append(url)
        raise OSError("network down")

    monkeypatch.setattr(assets, "_download_once", failing)
    with pytest.raises(RuntimeError):
        assets.download("http://example/x.bin", paths.models_dir() / "x.bin", attempts=3)
    assert len(calls) == 3


def test_download_retry_succeeds_on_second_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    attempts = {"n": 0}

    def flaky(url, dest, progress=None, timeout=60.0):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("transient")
        return dest

    monkeypatch.setattr(assets, "_download_once", flaky)
    result = assets.download("http://example/x.bin", paths.models_dir() / "x.bin", attempts=3)
    assert result.name == "x.bin"
    assert attempts["n"] == 2
