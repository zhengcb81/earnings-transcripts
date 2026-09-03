"""单实例锁。

scraper / translate 都会写同一批文件（英文原文、bilingual JSON、翻译缓存）。
两个实例并发跑会互相覆盖、重复烧 token，还会把 .translate_cache.json 写坏。
这里用文件锁做互斥：进程正常退出或崩溃时，操作系统会自动释放锁，不留死锁。
"""

import os
import atexit
from pathlib import Path
from datetime import datetime

try:
    import msvcrt                      # Windows
except ImportError:                    # pragma: no cover - 非 Windows
    msvcrt = None
    try:
        import fcntl                   # POSIX
    except ImportError:
        fcntl = None


# 只锁第 0 字节；持有者信息从第 1 字节开始写。
# Windows 的锁是强制锁——被锁的字节其它进程连读都会被拒绝，
# 所以提示信息必须落在锁区间之外，否则读提示本身就会抛 PermissionError。
_LOCK_BYTES = 1


def _read_holder(path: Path) -> str:
    """读取锁文件里记录的持有者信息（跳过被锁的第 0 字节）。"""
    try:
        with open(path, "rb") as fh:
            fh.seek(_LOCK_BYTES)
            info = fh.read(200).decode("utf-8", "ignore").strip()
    except (OSError, IOError):
        return "未知进程（无法读取锁文件）"
    return info or "未知进程（上次可能异常退出）"


class SingleInstanceLock:
    """跨进程互斥锁。用法：acquire() 返回 (True, "") 才可继续。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> tuple[bool, str]:
        """获取锁。失败时返回 (False, 持有者信息)，调用方应停止并提示用户。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")

        try:
            self._fh.seek(0)
            if msvcrt is not None:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, _LOCK_BYTES)
            elif fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            self._fh.close()
            self._fh = None
            return False, _read_holder(self.path)

        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(b"\0" * _LOCK_BYTES)
        self._fh.write(
            f"pid={os.getpid()} started={datetime.now().isoformat(timespec='seconds')}".encode()
        )
        self._fh.flush()
        atexit.register(self.release)
        return True, ""

    def release(self):
        if self._fh is None:
            return
        try:
            self._fh.seek(_LOCK_BYTES)
            self._fh.truncate()          # 清掉持有者信息，避免误导
            self._fh.flush()
        except (OSError, IOError):
            pass
        try:
            self._fh.seek(0)
            if msvcrt is not None:
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        ok, holder = self.acquire()
        if not ok:
            raise RuntimeError(holder)
        return self

    def __exit__(self, *exc):
        self.release()
