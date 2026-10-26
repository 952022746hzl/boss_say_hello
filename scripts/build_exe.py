"""Build a fresh, private-data-free Windows folder distribution."""

import os
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def verify_runtime(bundle, probe, work):
    import json

    # A relocated copy and a minimal environment prevent use of source or developer tools.
    work = work / ("verification-" + uuid4().hex[:8])
    folder = work / "便携验证" / "BossSayHello"
    shutil.copytree(bundle, folder)
    shutil.copy2(probe, folder / "PortableProbe.exe")
    cwd = work / "unrelated-cwd"
    cwd.mkdir()
    report = work / "runtime-verification.json"
    keys = {"SYSTEMROOT", "WINDIR", "PROGRAMDATA"}
    env = {k: v for k, v in os.environ.items() if k.upper() in keys}
    env["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    for key, relative in {"USERPROFILE": "profile", "LOCALAPPDATA": "profile/AppData/Local",
                          "APPDATA": "profile/AppData/Roaming", "TEMP": "temp", "TMP": "temp"}.items():
        directory = work / relative
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    process = subprocess.Popen([str(folder / "PortableProbe.exe"), str(report)], cwd=cwd, env=env,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        returncode = process.wait(timeout=90)
    except subprocess.TimeoutExpired:
        # Keep the parent alive until taskkill has found its descendants. Never kill by image name.
        if process.poll() is None:
            subprocess.run([str(Path(env["PATH"]) / "taskkill.exe"), "/PID", str(process.pid), "/T", "/F"],
                           creationflags=subprocess.CREATE_NO_WINDOW, timeout=15, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise RuntimeError(f"Portable runtime verification timed out. See {report}") from None
    details = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    if returncode or not details.get("ok") or (cwd / "workspace").exists():
        raise RuntimeError(f"Portable runtime verification failed. See {report}: {details.get('error', '')}")
    print(f"Offline portable runtime verification passed: {report}")


def verify_inputs(project, entries):
    # Check source paths too: renaming a private file must not bypass the guard.
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_release_files import check_release_files

    project = Path(project).resolve()
    dependencies = project / ".venv" / "Lib" / "site-packages"
    rejected = []
    for destination, source, kind in entries:
        origin = Path(source).resolve()
        if origin.is_relative_to(dependencies):
            source_name = origin.relative_to(dependencies).as_posix()
        elif origin.is_relative_to(project):
            source_name = origin.relative_to(project).as_posix()
        else:
            source_name = origin.as_posix()
        rejected.extend(check_release_files([source_name]))
        if kind not in {"PYMODULE", "PYSOURCE", "PYMODULE-1", "PYMODULE-2"}:
            rejected.extend(check_release_files([destination]))
    if rejected:
        raise ValueError("Refusing to package private files: " + ", ".join(sorted(set(rejected))))


def main():
    if sys.platform != "win32" or sys.maxsize <= 2**32 or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise SystemExit("Build this package using 64-bit Python on Windows x64.")
    # DLL discovery must not pick up unrelated apps' copies from the caller's PATH.
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    os.environ["PATH"] = os.pathsep.join(map(str, [
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        windows / "System32", windows,
    ]))
    from bundle_browser import bundle_browser
    from PyInstaller.__main__ import run

    # Never rebuild over a folder that may contain the user's workspace.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    work = ROOT / "build" / stamp
    output = ROOT / "dist" / stamp
    work.mkdir(parents=True)
    spec = work / "BossSayHello.spec"
    spec.write_text(f'''
import sys
sys.path.insert(0, {str(ROOT / "scripts")!r})
from build_exe import verify_inputs
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

a = Analysis(
    [{str(ROOT / "src/boss_say_hello/__main__.py")!r}],
    pathex=[{str(ROOT / "src")!r}],
    binaries=[], datas=copy_metadata("keyring"),
    hiddenimports=["keyring.backends.Windows"],
    excludes=["pytest", "pytestqt", "tkinter"],
)
verify_inputs({str(ROOT)!r}, [*a.scripts, *a.pure, *a.binaries, *a.datas])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True,
          name="BossSayHello", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="BossSayHello", upx=False)
# The verification entry runs against exactly the release PYZ and runtime hooks.
probe_scripts = [entry for entry in a.scripts if Path(entry[1]).resolve() != Path({str(ROOT / "src/boss_say_hello/__main__.py")!r})]
probe_scripts.append(("portable_probe", {str(ROOT / "scripts/portable_probe.py")!r}, "PYSOURCE"))
probe = EXE(pyz, probe_scripts, [], exclude_binaries=True, name="PortableProbe", console=False, upx=False)
Path({str(work / "probe-path.txt")!r}).write_text(probe.name, encoding="utf-8")
''', encoding="utf-8")
    run([str(spec), "--distpath", str(output), "--workpath", str(work), "--noconfirm"])
    bundle = output / "BossSayHello"
    bundle_browser(bundle, ROOT / ".cache" / "browser-download")
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    verify_inputs(bundle, [(p.relative_to(bundle).as_posix(), str(p), "DATA")
                           for p in bundle.rglob("*") if p.is_file()])
    verify_runtime(bundle, Path((work / "probe-path.txt").read_text(encoding="utf-8")), work)
    print(f"\nBuild complete: {bundle / 'BossSayHello.exe'}")
    print("Keep the entire BossSayHello folder. workspace is created beside the exe on first launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
