"""
tests/test_lock.py - 单实例锁测试

并发跑两个 scraper / translate 会互相覆盖文件、重复消耗翻译额度，
锁是整个增量机制能成立的前提。
"""

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from lock import SingleInstanceLock


class TestSingleInstanceLock:
    def test_acquire_and_release(self, tmp_path):
        """正常获取与释放，释放后锁可被重新获取。"""
        p = tmp_path / "a.lock"
        lock = SingleInstanceLock(p)
        ok, holder = lock.acquire()
        assert ok is True
        assert holder == ""
        assert p.exists()

        lock.release()
        assert SingleInstanceLock(p).acquire()[0] is True

    def test_second_instance_refused(self, tmp_path):
        """已有人持锁时，第二个实例必须被拒绝，且能拿到持有者信息。"""
        p = tmp_path / "b.lock"
        first = SingleInstanceLock(p)
        assert first.acquire()[0] is True
        try:
            ok, holder = SingleInstanceLock(p).acquire()
            assert ok is False
            assert "pid=" in holder          # 提示信息要能指出是谁占着
        finally:
            first.release()

    def test_stale_lock_is_taken_over(self, tmp_path):
        """锁文件残留但无人持有时（上次崩溃退出），应能接管而不是永久卡死。"""
        p = tmp_path / "c.lock"
        p.write_bytes(b"\0" + b"pid=999999 started=2020-01-01T00:00:00")
        assert SingleInstanceLock(p).acquire()[0] is True

    def test_holder_info_outside_locked_range(self, tmp_path):
        """持有者信息必须写在被锁字节之外，否则 Windows 强制锁会连读都拒绝。"""
        p = tmp_path / "d.lock"
        lock = SingleInstanceLock(p)
        assert lock.acquire()[0] is True
        try:
            # 从别的句柄读时必须越过第 0 字节（被锁区），否则 Windows 强制锁会拒绝
            with open(p, "rb") as fh:
                fh.seek(1)
                info = fh.read()
            assert info.startswith(b"pid=")
        finally:
            lock.release()

    def test_context_manager_raises_on_conflict(self, tmp_path):
        """with 语法在冲突时应抛出 RuntimeError 而不是静默继续。"""
        p = tmp_path / "e.lock"
        with SingleInstanceLock(p):
            with pytest.raises(RuntimeError):
                with SingleInstanceLock(p):
                    pass
