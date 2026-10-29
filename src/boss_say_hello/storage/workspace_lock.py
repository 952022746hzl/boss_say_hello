"""Keep recovery and all workspace writes exclusive for the backend's lifetime."""

from pathlib import Path

from PyQt6.QtCore import QLockFile


class WorkspaceLock:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self._lock = QLockFile(str(self.root / ".workspace.lock"))
        # A live application may use this workspace indefinitely. Only recover
        # locks whose owning process exited, never ones that are merely old.
        self._lock.setStaleLockTime(0)

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        if self._lock.tryLock(0):
            return
        if self._lock.error() == QLockFile.LockError.LockFailedError:
            raise ValueError(f"该工作目录正在使用中，请先关闭使用此目录的其他程序实例：{self.root}")
        raise ValueError(f"无法锁定工作目录，请检查目录权限和可用磁盘空间：{self.root}")

    def release(self):
        self._lock.unlock()
