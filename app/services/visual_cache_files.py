"""视觉缓存文件访问：固定目录句柄，防止检查后目录被替换为链接。"""

from contextlib import contextmanager
import os
from pathlib import Path
import stat


@contextmanager
def _windows_handle(path, *, read=False):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    info.restype = wintypes.BOOL
    # 共享读写，但不共享删除：持有期间不能重命名或替换目录。
    handle = create(str(path), 0x80000000 if read else 0x80, 3, None, 3, 0x02200000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = (wintypes.DWORD * 2)()
        if not info(handle, 9, attributes, ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes[0] & 0x400:
            raise ValueError("unsafe_cache_reparse_point")
        yield handle
    finally:
        close(handle)


class CacheFiles:
    def __init__(self, directory, fd=None):
        self.directory, self.fd = directory, fd

    def entries(self):
        with os.scandir(self.fd if self.fd is not None else self.directory) as entries:
            return [(p.name, p.stat(follow_symlinks=False)) for p in entries]

    def remove(self, name):
        if self.fd is None:
            path = self.directory / name
            if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("unsafe_cache_entry")
            path.unlink()
        else:
            if not stat.S_ISREG(os.stat(name, dir_fd=self.fd, follow_symlinks=False).st_mode):
                raise ValueError("unsafe_cache_entry")
            os.unlink(name, dir_fd=self.fd)

    def read(self, name, maximum):
        if self.fd is not None:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.fd)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("unsafe_cache_entry")
                return stream.read(maximum + 1)
        import ctypes
        from ctypes import wintypes
        with _windows_handle(self.directory / name, read=True) as handle:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            read = kernel.ReadFile
            read.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
            read.restype = wintypes.BOOL
            buffer = ctypes.create_string_buffer(maximum + 1)
            count = wintypes.DWORD()
            if not read(handle, buffer, maximum + 1, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return buffer.raw[:count.value]


@contextmanager
def locked_cache_files(directory):
    from contextlib import ExitStack

    directory = Path(os.path.abspath(directory))
    with ExitStack() as stack:
        if os.name == "nt":
            for path in (*reversed(directory.parents), directory):
                stack.enter_context(_windows_handle(path))
            yield CacheFiles(directory)
        else:
            fd = os.open(directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd)
            for part in directory.parts[1:]:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                stack.callback(os.close, fd)
            yield CacheFiles(directory, fd)
