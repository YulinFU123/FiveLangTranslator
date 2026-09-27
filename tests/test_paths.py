from app.core import paths


def test_app_root_is_repo_root_in_source_mode():
    assert not paths.is_frozen()
    root = paths.app_root()
    assert (root / "app").is_dir()
    assert (root / "pyproject.toml").is_file()


def test_data_root_honours_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "flt-home"))
    root = paths.data_root()
    assert root == tmp_path / "flt-home"
    assert root.is_dir()


def test_resource_root_prefers_populated_user_data(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "home"))
    # No downloaded assets yet -> fall back to the bundled/source root.
    assert paths.resource_root() == paths.app_root()
    # Once the downloader drops files in, the user directory wins.
    (paths.models_dir() / "ggml-small.bin").write_bytes(b"x")
    assert paths.resource_root() == paths.data_root()
