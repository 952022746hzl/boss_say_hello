import sqlite3


def test_workspace_migrates_local_data_once_without_changing_original(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    program = tmp_path / "program"
    program.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"model":"old-model"}', encoding="utf-8")
    (legacy / "resumes").mkdir()
    (legacy / "resumes" / "synthetic.pdf").write_bytes(b"synthetic")
    (legacy / "browser_profile").mkdir()
    (legacy / "browser_profile" / "Preferences").write_text("{}")
    db = sqlite3.connect(legacy / "records.sqlite3")
    db.execute("CREATE TABLE sample (value TEXT)")
    db.execute("INSERT INTO sample VALUES ('retained')")
    db.commit()
    db.close()
    monkeypatch.setattr(paths, "application_dir", lambda: program, raising=False)
    monkeypatch.setattr(paths, "user_data_path", lambda *args, **kwargs: legacy)
    root = paths.data_root()
    assert root == program / "workspace"
    assert (root / "config" / "settings.json").read_text() == '{"model":"old-model"}'
    assert (root / "resumes" / "synthetic.pdf").read_bytes() == b"synthetic"
    with sqlite3.connect(root / "records.sqlite3") as reopened:
        assert reopened.execute("SELECT value FROM sample").fetchone() == ("retained",)
    (root / "config" / "settings.json").write_text('{"model":"new-model"}')
    assert paths.data_root() == root
    assert "new-model" in (root / "config" / "settings.json").read_text()
    assert "old-model" in (legacy / "settings.json").read_text()
    assert (legacy / "browser_profile" / "Preferences").exists()


def test_workspace_has_stable_program_relative_path_when_no_legacy(tmp_path, monkeypatch):
    from boss_say_hello.storage import paths

    monkeypatch.setattr(paths, "application_dir", lambda: tmp_path, raising=False)
    monkeypatch.setattr(paths, "user_data_path", lambda *args, **kwargs: tmp_path / "missing")
    root = paths.data_root()
    assert root == tmp_path / "workspace"
    assert root.is_dir()
