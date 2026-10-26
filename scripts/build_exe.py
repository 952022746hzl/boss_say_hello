"""Build a fresh, private-data-free Windows folder distribution."""

import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


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
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")
    # DLL discovery must not pick up unrelated apps' copies from the caller's PATH.
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    os.environ["PATH"] = os.pathsep.join(map(str, [
        Path(sys.executable).parent, Path(sys.base_prefix), Path(sys.base_prefix) / "DLLs",
        windows / "System32", windows,
    ]))
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
''', encoding="utf-8")
    run([str(spec), "--distpath", str(output), "--workpath", str(work), "--noconfirm"])
    bundle = output / "BossSayHello"
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    verify_inputs(bundle, [(p.relative_to(bundle).as_posix(), str(p), "DATA")
                           for p in bundle.rglob("*") if p.is_file()])
    print(f"\nBuild complete: {bundle / 'BossSayHello.exe'}")
    print("Keep the entire BossSayHello folder. workspace is created beside the exe on first launch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
