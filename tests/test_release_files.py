import importlib.util
from pathlib import Path


def checker():
    spec = importlib.util.spec_from_file_location(
        "release_check", Path(__file__).parents[1] / "scripts/check_release_files.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check_release_files


def test_private_resume_and_env_rejected():
    paths = [
        "docs/spec.md",
        "DOCS/a.html",
        "nested/.ENV.example",
        "test.env.local",
        "env.production",
        "data/settings.json",
        "resume/mine.pdf",
        "browser_profile/Cookies",
        "records.sqlite3",
        "src/secret.envrc",
    ]
    assert checker()(paths) == paths


def test_source_resume_module_allowed():
    assert (
        checker()(["src/boss_say_hello/resume/pdf.py", "tests/test_resume.py", "README.md", "uv.lock"]) == []
    )


def test_archive_wrapper_cannot_hide_private_files():
    assert checker()(["boss_say_hello-0.1.0/docs/spec.md", "boss_say_hello-0.1.0/data/key.json"]) == [
        "boss_say_hello-0.1.0/docs/spec.md",
        "boss_say_hello-0.1.0/data/key.json",
    ]
