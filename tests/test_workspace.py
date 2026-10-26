import pytest


@pytest.fixture(autouse=True)
def isolate_legacy_data(tmp_path, monkeypatch):
    # An older installed version must never pull real user data into a test.
    monkeypatch.setattr(
        "boss_say_hello.storage.paths.user_data_path", lambda *args, **kwargs: tmp_path / "missing", raising=False
    )


def test_workspace_is_fresh_beside_exe_even_with_legacy_data(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    program = tmp_path / "求职助手"
    program.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"model":"old-model"}', encoding="utf-8")
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(program / "BossSayHello.exe"))
    monkeypatch.setattr(paths, "user_data_path", lambda *args, **kwargs: legacy, raising=False)
    other = tmp_path / "shortcut-start-in"
    other.mkdir()
    monkeypatch.chdir(other)
    root = paths.data_root()
    assert root == program / "workspace"
    assert list(root.iterdir()) == []
    assert not (other / "workspace").exists()
    (root / "keep.txt").write_text("retained")
    assert paths.data_root() == root
    assert (root / "keep.txt").read_text() == "retained"
    assert (legacy / "settings.json").exists()


def test_workspace_has_stable_program_relative_path_when_no_legacy(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    monkeypatch.setattr(paths, "application_dir", lambda: tmp_path, raising=False)
    root = paths.data_root()
    assert root == tmp_path / "workspace"
    assert root.is_dir()


def test_installed_launcher_uses_its_directory_instead_of_cwd(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    monkeypatch.setattr(paths, "__file__", str(tmp_path / "site-packages/pkg/storage/paths.py"))
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    monkeypatch.setattr(paths.sys, "argv", [str(tmp_path / "launcher" / "boss-say-hello.exe")])
    other = tmp_path / "unrelated"
    other.mkdir()
    monkeypatch.chdir(other)
    assert paths.data_root() == tmp_path / "launcher" / "workspace"
    assert not (other / "workspace").exists()


def test_source_launch_keeps_project_workspace_from_another_cwd(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").touch()
    monkeypatch.setattr(paths, "__file__", str(project / "src/boss_say_hello/storage/paths.py"))
    monkeypatch.setattr(paths.sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.data_root() == project / "workspace"
