"""Browser selection and subprocess environment for portable Windows builds."""

import ctypes
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from boss_say_hello.browser.port import BrowserFailure
from boss_say_hello.storage.paths import application_dir


def bundled_executable() -> Path | None:
    executable = application_dir() / "browser" / "chrome-win64" / "chrome.exe"
    if executable.is_file():
        return executable
    if getattr(sys, "frozen", False):
        raise BrowserFailure("随包浏览器缺失，请重新解压完整程序目录，并保留 browser 文件夹")
    return None


@contextmanager
def browser_process_environment():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return
    # PyInstaller and Qt alter DLL discovery; Chrome carries its own libraries.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    previous = ctypes.create_unicode_buffer(32768)
    kernel.GetDllDirectoryW(len(previous), previous)
    original_path = os.environ.get("PATH")
    internal = Path(getattr(sys, "_MEIPASS", application_dir() / "_internal")).resolve()
    if not kernel.SetDllDirectoryW(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        os.environ["PATH"] = os.pathsep.join(
            entry for entry in (original_path or "").split(os.pathsep)
            if entry and not Path(entry).resolve().is_relative_to(internal)
        )
        yield
    finally:
        kernel.SetDllDirectoryW(previous.value or None)
        if original_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = original_path


async def start_browser(profile: Path, *, headless: bool = False):
    import nodriver

    executable = bundled_executable()
    profile.mkdir(parents=True, exist_ok=True)
    with browser_process_environment():
        return await nodriver.start(
            user_data_dir=str(profile), headless=headless,
            browser_executable_path=str(executable) if executable else None,
        )
