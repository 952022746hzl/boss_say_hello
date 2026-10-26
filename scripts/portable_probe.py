"""Offline build verification entry; shares the release PYZ, never shipped to users."""

import asyncio
import ctypes
import io
import json
import re
import ssl
import sys
import traceback
from pathlib import Path

import certifi
import httpx
import keyring
from openai import AsyncOpenAI
from pypdf import PdfReader, PdfWriter
from PyQt6.QtWidgets import QApplication, QWidget

from boss_say_hello.browser.nodriver_adapter import NodriverAdapter
from boss_say_hello.browser.runtime import start_browser
from boss_say_hello.storage.paths import application_dir, data_root


def check_loaded_libraries(pid=None):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    psapi.EnumProcessModules.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p]
    psapi.GetModuleFileNameExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    modules = (ctypes.c_void_p * 2048)()
    needed = ctypes.c_ulong()
    found = []
    process = kernel.OpenProcess(0x410, False, pid) if pid else kernel.GetCurrentProcess()
    assert process
    try:
        assert psapi.EnumProcessModules(process, modules, ctypes.sizeof(modules), ctypes.byref(needed))
        for module in modules[:needed.value // ctypes.sizeof(ctypes.c_void_p)]:
            name = ctypes.create_unicode_buffer(32768)
            assert psapi.GetModuleFileNameExW(process, module, name, len(name))
            path = Path(name.value)
            # msvcp_win.dll is part of Windows itself, not the VC++ redistributable.
            if re.match(r"(vcruntime|msvcp|concrt)\d|libssl|libcrypto", path.name.lower()):
                assert path.is_relative_to(application_dir()), f"External runtime loaded: {path}"
                found.append(path.name)
    finally:
        if pid:
            kernel.CloseHandle(process)
    if pid is None:
        assert any(n.lower().startswith("vcruntime") for n in found)
        assert any(n.lower().startswith("msvcp") for n in found)
        assert any(n.lower().startswith("libssl") for n in found)
    return sorted(found)


async def exercise(report):
    ssl.create_default_context(cafile=certifi.where())
    report["tls"] = True
    backend = keyring.get_keyring()
    assert type(backend).__module__ == "keyring.backends.Windows"
    assert backend.priority > 0
    report["credential_backend"] = type(backend).__name__
    document = PdfWriter()
    document.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    document.write(stream)
    stream.seek(0)
    assert PdfReader(stream).pages[0].extract_text() == ""
    report["pdf"] = True

    def respond(request):
        return httpx.Response(200, json={"id": "offline", "model": "offline", "object": "chat.completion",
                                       "created": 0, "choices": [{"index": 0, "finish_reason": "stop",
                                       "message": {"role": "assistant", "content": "verified"}}]})

    async with AsyncOpenAI(api_key="synthetic", base_url="https://offline.invalid/v1",
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        result = await client.chat.completions.create(model="offline", messages=[{"role": "user", "content": "x"}])
        assert result.choices[0].message.content == "verified"
    report["model_sdk_offline"] = True
    adapter = NodriverAdapter()
    try:
        adapter.browser = await start_browser(data_root() / "probe_profile", headless=True)
        adapter.list_tab = await adapter.browser.get("data:text/html,<h1 id='probe'>portable-ok</h1>")
        result = await adapter.evaluate(adapter.list_tab, "JSON.stringify({text:document.querySelector('#probe').textContent})")
        assert result == {"text": "portable-ok"}
        browser_path = Path(adapter.browser.config.browser_executable_path)
        assert browser_path == application_dir() / "browser/chrome-win64/chrome.exe"
        report["bundled_browser_cdp"] = True
        report["browser_runtime_libraries"] = check_loaded_libraries(adapter.browser._process.pid)
    finally:
        await adapter.close()


def main():
    report = {"ok": False}
    app = None
    try:
        assert getattr(sys, "frozen", False), "Probe must run frozen, with the release PYZ"
        app = QApplication([])
        widget = QWidget()
        widget.setWindowTitle("Portable runtime verification")
        app.processEvents()
        assert widget.windowTitle() == "Portable runtime verification"
        report["qt"] = True
        assert data_root() == application_dir() / "workspace"
        asyncio.run(asyncio.wait_for(exercise(report), timeout=60))
        report["bundled_libraries"] = check_loaded_libraries()
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if app:
            app.quit()
        Path(sys.argv[1]).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
