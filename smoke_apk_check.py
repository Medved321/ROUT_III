"""Регрессионная проверка tools/apk_check.py на синтетических APK.

Тест ловит ровно те ошибки, из-за которых инструмент врал бы пользователю:
неверный разбор ELF32/ELF64, пропуск архива Python как «библиотеки» и
обратную Verdikt для 4 КБ/16 КБ выравнивания.
"""
from __future__ import annotations

import struct
import tempfile
import zipfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import apk_check  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def elf64(align: int) -> bytes:
    """Минимальный ELF64 с одним PT_LOAD и нужным p_align."""
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4] = 2
    struct.pack_into("<Q", data, 32, 64)  # e_phoff
    struct.pack_into("<HH", data, 54, 56, 1)  # e_phentsize, e_phnum
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 0, 0, align)
    return bytes(data + phdr)


def elf32(align: int) -> bytes:
    data = bytearray(52)
    data[:4] = b"\x7fELF"
    data[4] = 1
    struct.pack_into("<I", data, 28, 52)
    struct.pack_into("<HH", data, 42, 32, 1)
    phdr = struct.pack("<IIIIIIII", 1, 0, 0, 0, 0, 0, 5, align)
    return bytes(data + phdr)


def make_apk(path: Path, payloads: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in payloads.items():
            zf.writestr(name, payload)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        good = root / "good.apk"
        make_apk(good, {
            "lib/arm64-v8a/libmain.so": elf64(0x4000),
            "lib/arm64-v8a/libpybundle.so": b"not ELF, just a Python archive",
        })
        ok, lines = apk_check.check_apk(good)
        check(ok, "16K APK must be accepted")
        check("OK 16K" in "\n".join(lines), "a 16K library must be marked")
        check("пропущен" in "\n".join(lines),
              "libpybundle.so is a Python archive, not a library")

        bad = root / "bad.apk"
        make_apk(bad, {"lib/arm64-v8a/libmain.so": elf64(0x1000)})
        ok, lines = apk_check.check_apk(bad)
        check(not ok, "a 4K-only 64-bit library must be rejected")
        check("ТОЛЬКО 4K" in "\n".join(lines), "the verdict must name the cause")

        old32 = root / "arm32.apk"
        make_apk(old32, {"lib/armeabi-v7a/libmain.so": elf32(0x1000)})
        ok, _ = apk_check.check_apk(old32)
        check(ok, "32-bit libraries are not required to be 16K-aligned")

        broken = root / "broken.apk"
        make_apk(broken, {"lib/arm64-v8a/libmain.so": b"MZ\x00\x00"})
        ok, lines = apk_check.check_apk(broken)
        check(not ok, "a non-ELF library must not be silently accepted")
        check("НЕ ЧИТАЕТСЯ" in "\n".join(lines), "the report must say it is unreadable")

    print("apk_check: ELF32/ELF64, 16K verdict and Python bundle skip OK")


if __name__ == "__main__":
    main()
