import importlib.util
from pathlib import Path

import pytest


def builder():
    path = Path(__file__).parents[1] / "scripts" / "build_exe.py"
    spec = importlib.util.spec_from_file_location("build_exe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("private", ["docs/spec.md", ".env", "src/demo.env.example", "workspace/config/settings.json"])
def test_packaging_rejects_private_inputs_even_when_destination_is_renamed(tmp_path, private):
    source = tmp_path / private
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("synthetic private data")
    with pytest.raises(ValueError, match="private"):
        builder().verify_inputs(tmp_path, [("innocent.txt", str(source), "DATA")])


def test_packaging_allows_program_and_dependency_inputs(tmp_path):
    builder().verify_inputs(tmp_path, [
        ("boss_say_hello.app", str(tmp_path / "src/boss_say_hello/app.py"), "PYMODULE"),
        ("PyQt6/Qt6/plugins/platforms/qwindows.dll", str(tmp_path / ".venv/Lib/site-packages/PyQt6/qwindows.dll"), "BINARY"),
    ])


def test_packaging_rejects_private_destination_from_external_source(tmp_path):
    with pytest.raises(ValueError, match="private"):
        builder().verify_inputs(tmp_path / "repo", [("workspace/settings.json", str(tmp_path / "file"), "DATA")])
