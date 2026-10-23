"""Reject private documentation, environment files and runtime data in release lists."""

import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import PurePosixPath


def check_release_files(paths: list[str]) -> list[str]:
    rejected = []
    private_dirs = {
        "docs",
        ".venv",
        "venv",
        ".superpowers",
        ".cache",
        "browser_profile",
        "chrome_profile",
        "resumes",
        "workspace",
    }
    for path in paths:
        parts = PurePosixPath(path.replace("\\", "/").lower()).parts
        if not parts:
            continue
        name = parts[-1]
        root = list(parts)
        if re.match(r"boss[-_]say[-_]hello-\d", root[0]):
            root = root[1:]
        bad_env = any(re.search(r"(^\.?env(?:rc)?($|[._-])|\.env(?:rc)?($|[._-]))", p) for p in parts)
        bad_data = bool(root and root[0] in {"data", "logs", "resume"}) or any(
            re.search(r"\.(sqlite3?|db)(-|$)", p) for p in parts
        )
        browser_file = name in {"cookies", "login data", "local state", "web data"} or bool(
            re.search(r"\.log(?:\.\d+)?$", name)
        )
        if any(p in private_dirs for p in parts) or bad_env or bad_data or browser_file:
            rejected.append(path)
    return rejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true")
    group.add_argument("--tracked", action="store_true")
    group.add_argument("--package")
    args = parser.parse_args()
    if args.package:
        if zipfile.is_zipfile(args.package):
            with zipfile.ZipFile(args.package) as archive:
                paths = archive.namelist()
        else:
            with tarfile.open(args.package) as archive:
                paths = archive.getnames()
    else:
        cmd = ["git", "-c", "core.excludesFile=.gitignore"]
        cmd += (
            ["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]
            if args.staged
            else ["ls-files", "-z"]
        )
        paths = [p for p in subprocess.check_output(cmd).decode("utf-8").split("\0") if p]
    rejected = check_release_files(paths)
    if rejected:
        print("禁止提交或分发的路径：\n" + "\n".join(rejected))
        return 1
    print(f"通过：已检查 {len(paths)} 个路径，无 docs、env 或运行数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
