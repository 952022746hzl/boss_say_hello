import ctypes
import os
import sys

import pytest


async def test_frozen_browser_uses_bundled_executable_even_without_system_chrome(tmp_path, monkeypatch):
    import nodriver

    from boss_say_hello.browser import runtime

    executable = tmp_path / "browser/chrome-win64/chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setattr(runtime, "application_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    async def prepare(**kwargs):
        # Exercise nodriver's actual configuration validation, without launching a process.
        return nodriver.Config(**kwargs)

    monkeypatch.setattr(nodriver, "start", prepare)
    config = await runtime.start_browser(tmp_path / "profile")
    assert config.browser_executable_path == str(executable)
    assert config.user_data_dir == str(tmp_path / "profile")


def test_frozen_browser_missing_does_not_silently_use_system_installation(tmp_path, monkeypatch):
    from boss_say_hello.browser import runtime
    from boss_say_hello.browser.port import BrowserFailure

    monkeypatch.setattr(runtime, "application_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(BrowserFailure, match="browser"):
        runtime.bundled_executable()


def test_source_can_keep_using_installed_chrome(tmp_path, monkeypatch):
    from boss_say_hello.browser import runtime

    monkeypatch.setattr(runtime, "application_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert runtime.bundled_executable() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL search semantics")
def test_browser_launch_restores_dll_search_and_path_after_error(tmp_path, monkeypatch):
    from boss_say_hello.browser import runtime

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    before = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(before), before)
    internal = tmp_path / "_internal"
    internal.mkdir()
    clean = str(tmp_path / "other")
    original_path = os.pathsep.join([str(internal), str(internal / "bin"), clean])
    monkeypatch.setenv("PATH", original_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    try:
        kernel.SetDllDirectoryW(str(internal))
        with pytest.raises(RuntimeError, match="synthetic"):
            with runtime.browser_process_environment():
                current = ctypes.create_unicode_buffer(32768)
                kernel.GetDllDirectoryW(len(current), current)
                assert current.value == ""
                assert os.environ["PATH"] == clean
                raise RuntimeError("synthetic")
        current = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(current), current)
        assert current.value == str(internal)
        assert os.environ["PATH"] == original_path
    finally:
        kernel.SetDllDirectoryW(before.value or None)
