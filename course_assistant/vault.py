"""Secret storage bound to the current Windows user; no plaintext fallback."""
from __future__ import annotations
import ctypes
import os
from ctypes import wintypes
from pathlib import Path


class Vault:
    def __init__(self, path: Path):
        self.path = path

    def exists(self):
        return self.path.is_file()

    def save(self, value: str):
        if not value.strip():
            raise ValueError("密钥不能为空。")
        if any(ord(c) < 33 or ord(c) > 126 for c in value.strip()):
            raise ValueError("密钥包含空格、换行或非英文字符，请重新复制完整密钥。")
        data = self._crypt(value.strip().encode(), decrypt=False)
        temp = self.path.with_suffix(".tmp")
        temp.write_bytes(data)
        os.replace(temp, self.path)

    def read(self):
        return self._crypt(self.path.read_bytes(), decrypt=True).decode() if self.exists() else ""

    def clear(self):
        self.path.unlink(missing_ok=True)

    @staticmethod
    def _crypt(data: bytes, decrypt: bool) -> bytes:
        if os.name != "nt":
            raise ValueError("密钥持久保存目前仅支持 Windows 当前用户加密。")
        class Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        fn.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise ValueError("Windows 加密操作失败；请使用保存密钥的同一 Windows 用户。")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)
