import zipfile

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
