"""Bundle the pinned official Chrome for Testing runtime during Windows builds."""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

CHROME_VERSION = "156.0.8078.47"
CHROME_URL = (
    f"https://storage.googleapis.com/chrome-for-testing-public/{CHROME_VERSION}/win64/chrome-win64.zip"
)
CHROME_SHA256 = "a4e0d57245e167e98a9d465af1e3774d2b4ec9f04e483d19bcd97a6f63cf46dd"
CHROME_ARCHIVE = f"chrome-win64-{CHROME_VERSION}.zip"
_EXECUTABLE = "chrome-win64/chrome.exe"
_RESERVED = re.compile(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]", re.IGNORECASE)


def _verify_sha256(path: Path) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != CHROME_SHA256:
        raise ValueError(f"Chrome archive SHA256 mismatch: {path.name}")


def _cached_archive(cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / CHROME_ARCHIVE
    if os.path.lexists(archive):
        if archive.is_symlink() or not archive.is_file():
            raise ValueError("Chrome cache must be a regular ZIP file")
        _verify_sha256(archive)
        return archive

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cache, prefix="chrome-", suffix=".download", delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(CHROME_URL, timeout=120) as response:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        _verify_sha256(temporary)
        temporary.replace(archive)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return archive


def _release_guard(paths: list[str]) -> None:
    # Loading the sibling by file path also works when this module is imported by a spec/test.
    path = Path(__file__).with_name("check_release_files.py")
    spec = importlib.util.spec_from_file_location("_browser_release_guard", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rejected = module.check_release_files(paths)
    if rejected:
        raise ValueError("Refusing to package private browser paths: " + ", ".join(rejected))


def _validated_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    _release_guard([member.filename for member in members])
    seen: dict[str, bool] = {}
    for member in members:
        name = member.filename
        parts = name.removesuffix("/").split("/")
        unsafe = (
            name != member.orig_filename
            or "\\" in name
            or parts[0] != "chrome-win64"
            or any(
                part in {"", ".", ".."}
                or part.rstrip(" .") != part
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or _RESERVED.fullmatch(part.split(".", 1)[0])
                for part in parts
            )
        )
        mode = stat.S_IFMT(member.external_attr >> 16)
        special = mode not in {0, stat.S_IFREG, stat.S_IFDIR} or bool(member.external_attr & 0x400)
        wrong_type = (mode == stat.S_IFDIR and not member.is_dir()) or (
            mode == stat.S_IFREG and member.is_dir()
        )
        if unsafe or special or wrong_type:
            raise ValueError(f"Unsafe browser ZIP member: {name!r}")
        canonical = "/".join(parts).casefold()
        if canonical in seen:
            raise ValueError(f"Duplicate browser ZIP path: {name!r}")
        seen[canonical] = member.is_dir()

    for name in seen:
        if any(str(parent) in seen and not seen[str(parent)] for parent in PurePosixPath(name).parents):
            raise ValueError(f"File/directory conflict in browser ZIP: {name!r}")
    if seen.get(_EXECUTABLE) is not False:
        raise ValueError("Chrome archive is missing chrome-win64/chrome.exe")
    return members


def _require_new_target(target: Path) -> None:
    if os.path.lexists(target):
        raise FileExistsError(f"Refusing to overwrite existing browser content: {target}")


def bundle_browser(bundle: Path, cache: Path) -> Path:
    """Return the bundled chrome.exe; cache is a directory, and browser must not exist."""
    bundle = Path(bundle)
    target = bundle / "browser"
    _require_new_target(target)
    cached = _cached_archive(Path(cache))
    with zipfile.ZipFile(cached) as archive:
        members = _validated_members(archive)
        bundle.mkdir(parents=True, exist_ok=True)
        # TemporaryDirectory uses a private Windows ACL that survives rename into the release.
        # A normal mkdir inherits the release directory's permissions for the complete browser tree.
        staging = bundle / f".browser-{uuid4().hex}"
        staging.mkdir()
        try:
            for member in members:
                destination = staging.joinpath(*member.filename.rstrip("/").split("/"))
                if not destination.resolve().is_relative_to(staging.resolve()):
                    raise ValueError(f"Browser ZIP member escapes staging: {member.filename!r}")
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, destination.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
            (staging / "runtime.json").write_text(
                json.dumps({"version": CHROME_VERSION, "url": CHROME_URL, "sha256": CHROME_SHA256}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            _require_new_target(target)
            staging.rename(target)
        finally:
            if staging.exists():
                if staging.resolve().parent != bundle.resolve():
                    raise ValueError("Refusing to clean a browser staging directory outside the bundle")
                shutil.rmtree(staging)
    return target / _EXECUTABLE
