import ctypes
import hashlib
import importlib.util
import io
import json
import shutil
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest


def helper():
    path = Path(__file__).parents[1] / "scripts" / "bundle_browser.py"
    assert path.is_file(), "Browser bundling helper has not been implemented"
    spec = importlib.util.spec_from_file_location("bundle_browser_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def archive_bytes(extra=(), *, executable=True):
    contents = [
        ("chrome-win64/chrome.dll", b"browser library"),
        ("chrome-win64/resources.pak", b"browser resources"),
        ("chrome-win64/locales/zh-CN.pak", b"Chinese locale"),
        ("chrome-win64/ABOUT", b"original license notice"),
        ("chrome-win64/LICENSE", b"original license text"),
    ]
    if executable:
        contents.insert(0, ("chrome-win64/chrome.exe", b"synthetic executable"))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in [*contents, *extra]:
            info = name if isinstance(name, zipfile.ZipInfo) else zipfile.ZipInfo(name)
            archive.writestr(info, content)
    return output.getvalue()


def cached_archive(tmp_path, monkeypatch, raw=None):
    module = helper()
    raw = archive_bytes() if raw is None else raw
    monkeypatch.setattr(module, "CHROME_SHA256", hashlib.sha256(raw).hexdigest())
    cache = tmp_path / "cache"
    cache.mkdir()
    archive = cache / module.CHROME_ARCHIVE
    archive.write_bytes(raw)

    def no_network(*args, **kwargs):
        pytest.fail("A cached browser must not trigger a network download")

    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    return module, cache, archive


def test_cached_browser_preserves_resources_and_licenses(tmp_path, monkeypatch):
    module, cache, archive = cached_archive(tmp_path, monkeypatch)
    bundle = tmp_path / "发行包 with spaces"
    executable = module.bundle_browser(bundle, cache)

    assert executable == bundle / "browser/chrome-win64/chrome.exe"
    assert executable.read_bytes() == b"synthetic executable"
    assert (executable.parent / "chrome.dll").read_bytes() == b"browser library"
    assert (executable.parent / "resources.pak").read_bytes() == b"browser resources"
    assert (executable.parent / "locales/zh-CN.pak").read_bytes() == b"Chinese locale"
    assert (executable.parent / "ABOUT").read_bytes() == b"original license notice"
    assert (executable.parent / "LICENSE").read_bytes() == b"original license text"
    metadata = json.loads((bundle / "browser/runtime.json").read_text(encoding="utf-8"))
    assert metadata["version"] == "156.0.8078.47"
    assert metadata["url"] == (
        "https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.47/win64/chrome-win64.zip"
    )
    assert metadata["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()


def test_corrupt_cached_archive_is_rejected_without_unpacking(tmp_path, monkeypatch):
    module, cache, archive = cached_archive(tmp_path, monkeypatch)
    archive.write_bytes(archive.read_bytes() + b"corruption")
    bundle = tmp_path / "bundle"

    with pytest.raises(ValueError, match="SHA256"):
        module.bundle_browser(bundle, cache)

    assert not (bundle / "browser").exists()
    assert archive.read_bytes().endswith(b"corruption")


@pytest.mark.parametrize("name", [
    "../escaped.txt",
    "chrome-win64/../../escaped.txt",
    "/escaped.txt",
    "C:/escaped.txt",
    "//server/share/escaped.txt",
    "chrome-win64\\..\\escaped.txt",
    "chrome-win64/./escaped.txt",
    "chrome-win64//escaped.txt",
    "chrome-win64/file:stream",
    "chrome-win64/CON.txt",
    "chrome-win64/file. ",
    "chrome-win64/docs/private.md",
    "chrome-win64/.env",
    "chrome-win64/config.env.example",
    "chrome-win64/env.template",
    "chrome-win64/.envrc.local",
    "chrome-win64/workspace/private.json",
    "another-root/file.txt",
])
def test_unsafe_members_are_rejected_before_any_extraction(tmp_path, monkeypatch, name):
    raw = archive_bytes([(name, b"must never be extracted")])
    module, cache, _ = cached_archive(tmp_path, monkeypatch, raw)
    bundle = tmp_path / "bundle"

    with pytest.raises(ValueError):
        module.bundle_browser(bundle, cache)

    assert not (bundle / "browser").exists()
    assert not (tmp_path / "escaped.txt").exists()
    assert not bundle.exists() or not list(bundle.iterdir())


@pytest.mark.parametrize("mode", [stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600])
def test_zip_links_and_special_files_are_rejected(tmp_path, monkeypatch, mode):
    info = zipfile.ZipInfo("chrome-win64/redirect")
    info.create_system = 3
    info.external_attr = mode << 16
    module, cache, _ = cached_archive(tmp_path, monkeypatch, archive_bytes([(info, b"../../outside")]))

    with pytest.raises(ValueError):
        module.bundle_browser(tmp_path / "bundle", cache)

    assert not (tmp_path / "bundle/browser").exists()


@pytest.mark.parametrize("extra", [
    [("chrome-win64/CHROME.EXE", b"replacement")],
    [("chrome-win64/locales", b"file conflicting with a directory")],
])
def test_windows_aliases_and_file_directory_conflicts_are_rejected(tmp_path, monkeypatch, extra):
    module, cache, _ = cached_archive(tmp_path, monkeypatch, archive_bytes(extra))

    with pytest.raises(ValueError):
        module.bundle_browser(tmp_path / "bundle", cache)

    assert not (tmp_path / "bundle/browser").exists()


@pytest.mark.parametrize("directory", [True, False])
def test_existing_browser_content_is_never_overwritten(tmp_path, monkeypatch, directory):
    module, cache, _ = cached_archive(tmp_path, monkeypatch)
    target = tmp_path / "bundle/browser"
    target.parent.mkdir()
    if directory:
        target.mkdir()
        existing = target / "user-content.txt"
    else:
        existing = target
    existing.write_text("keep me", encoding="utf-8")

    with pytest.raises(FileExistsError):
        module.bundle_browser(target.parent, cache)

    assert existing.read_text(encoding="utf-8") == "keep me"
    assert not (target / "chrome-win64").exists()


def test_archive_without_browser_executable_is_rejected(tmp_path, monkeypatch):
    module, cache, _ = cached_archive(tmp_path, monkeypatch, archive_bytes(executable=False))

    with pytest.raises(ValueError, match="chrome.exe"):
        module.bundle_browser(tmp_path / "bundle", cache)

    assert not (tmp_path / "bundle/browser").exists()


def test_missing_cache_is_downloaded_verified_and_reused(tmp_path, monkeypatch):
    module = helper()
    raw = archive_bytes()
    monkeypatch.setattr(module, "CHROME_SHA256", hashlib.sha256(raw).hexdigest())
    cache = tmp_path / "cache"

    def download(url, *, timeout):
        assert url == "https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.47/win64/chrome-win64.zip"
        assert timeout > 0
        assert not (cache / module.CHROME_ARCHIVE).exists()
        return io.BytesIO(raw)

    monkeypatch.setattr(urllib.request, "urlopen", download)
    assert module.bundle_browser(tmp_path / "first", cache).is_file()
    assert (cache / module.CHROME_ARCHIVE).read_bytes() == raw
    assert sorted(p.name for p in cache.iterdir()) == [module.CHROME_ARCHIVE]

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("cache was not reused"))
    assert module.bundle_browser(tmp_path / "second", cache).is_file()


def test_bad_download_is_not_published_or_cached(tmp_path, monkeypatch):
    module = helper()
    monkeypatch.setattr(module, "CHROME_SHA256", "0" * 64)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(archive_bytes()))
    cache = tmp_path / "cache"

    with pytest.raises(ValueError, match="SHA256"):
        module.bundle_browser(tmp_path / "bundle", cache)

    assert not (tmp_path / "bundle/browser").exists()
    assert not list(cache.iterdir())


def windows_dacl(path):
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ]
    advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_void_p,
    ]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    text = ctypes.c_void_p()
    result = advapi.GetNamedSecurityInfoW(str(path), 1, 4, None, None, None, None, ctypes.byref(descriptor))
    if result:
        raise ctypes.WinError(result)
    try:
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor, 1, 4, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.wstring_at(text)
    finally:
        kernel.LocalFree(text)
        kernel.LocalFree(descriptor)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows inherited directory ACLs")
def test_browser_inherits_release_directory_permissions(tmp_path, monkeypatch):
    module, cache, _ = cached_archive(tmp_path, monkeypatch)
    # pytest's tmp_path is deliberately private; use a normal directory under the project cache.
    cache_root = Path(__file__).parents[1] / ".cache"
    bundle = cache_root / f"browser-acl-{uuid4().hex}"
    bundle.mkdir()
    try:
        reference = bundle / "ordinary-directory"
        reference.mkdir()
        executable = module.bundle_browser(bundle, cache)
        assert windows_dacl(executable.parent.parent) == windows_dacl(reference)
        assert windows_dacl(executable.parent) == windows_dacl(reference)
        assert {path.name for path in bundle.iterdir()} == {"browser", "ordinary-directory"}
    finally:
        assert bundle.resolve().parent == cache_root.resolve()
        shutil.rmtree(bundle)


def test_failed_extraction_removes_only_its_own_staging_directory(tmp_path, monkeypatch):
    module, cache, _ = cached_archive(tmp_path, monkeypatch)
    bundle = tmp_path / "bundle"
    unrelated = bundle / ".browser-user-content"
    unrelated.mkdir(parents=True)
    marker = unrelated / "keep.txt"
    marker.write_text("untouched", encoding="utf-8")
    original_open = zipfile.ZipFile.open

    def broken_read(self, member, *args, **kwargs):
        if isinstance(member, zipfile.ZipInfo) and member.filename == "chrome-win64/chrome.dll":
            raise OSError("synthetic extraction error")
        return original_open(self, member, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", broken_read)
    with pytest.raises(OSError, match="synthetic extraction error"):
        module.bundle_browser(bundle, cache)
    assert marker.read_text(encoding="utf-8") == "untouched"
    assert list(bundle.iterdir()) == [unrelated]
