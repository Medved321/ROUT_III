r"""Проверить APK ROUT III без adb: ABI и выравнивание нативных библиотек.

Приложение на Android 16 может закрываться сразу, не оставив ни Java-журнала,
ни экрана с traceback: так выглядит несовместимость .so с 16 КБ страницами.
Этот скрипт проверяет ровно то, что решает судьбу APK на таком устройстве,
и работает по уже скачанному файлу — ничего скачивать не нужно:

    py -3.12 tools/apk_check.py C:\path\to\rout3-1.2.1-arm64-v8a-debug.apk

Для каждой библиотеки печатается максимальное выравнивание сегментов PT_LOAD.
Android с 16 КБ страницами требует минимум 0x4000 (16384) у 64-битных .so;
0x1000 (4096) означает, что библиотека грузится только на устройствах с 4 КБ.
"""
from __future__ import annotations

import argparse
import struct
import sys
import zipfile
from pathlib import Path

PT_LOAD = 1
MIN_16K_ALIGN = 0x4000
#: p4a called its Python bundle ``libpybundle.so`` but it is a zip archive that
#: ``PythonUtil.unpackPyBundle`` opens as a file; the linker never loads it.
PYTHON_BUNDLE = "libpybundle.so"


class ElfError(ValueError):
    """Файл не является читаемым ELF."""


def elf_load_alignments(data: bytes) -> tuple[int, list[int]]:
    """Вернуть (class, [p_align сегментов PT_LOAD]) из заголовков ELF."""
    if len(data) < 20 or data[:4] != b"\x7fELF":
        raise ElfError("не ELF")
    elf_class = data[4]
    if elf_class == 2:  # ELF64
        if len(data) < 64:
            raise ElfError("короткий ELF64")
        phoff = struct.unpack_from("<Q", data, 32)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        fmt = "<IIQQQQQQ"
    elif elf_class == 1:  # ELF32
        if len(data) < 52:
            raise ElfError("короткий ELF32")
        phoff = struct.unpack_from("<I", data, 28)[0]
        phentsize, phnum = struct.unpack_from("<HH", data, 42)
        fmt = "<IIIIIIII"
    else:
        raise ElfError(f"неизвестный класс ELF: {elf_class}")

    if phentsize < struct.calcsize(fmt):
        raise ElfError(f"неверный размерProgram Header: {phentsize}")

    alignments = []
    for index in range(phnum):
        start = phoff + index * phentsize
        if start + phentsize > len(data):
            raise ElfError("Program Header за концом файла")
        fields = struct.unpack_from(fmt, data, start)
        if fields[0] == PT_LOAD:
            # p_align — последнее поле и ELF32, и ELF64.
            alignments.append(int(fields[-1]))
    return elf_class, alignments


def check_apk(path: Path) -> tuple[bool, list[str]]:
    """Вернуть (годен ли APK для 16 КБ, строки отчёта)."""
    lines: list[str] = []
    if not path.is_file():
        return False, [f"файл не найден: {path}"]

    with zipfile.ZipFile(path) as zf:
        libs = sorted(n for n in zf.namelist()
                      if n.startswith("lib/") and n.endswith(".so"))
        if not libs:
            return False, ["в APK нет lib/**/*.so — это не наш APK"]

        abis = sorted({n.split("/")[1] for n in libs if len(n.split("/")) > 2})
        lines.append(f"APK: {path.name} ({path.stat().st_size / 1024 / 1024:.1f} MiB)")
        lines.append(f"ABI в APK: {', '.join(abis)}")
        lines.append("")

        ok = True
        for name in libs:
            data = zf.read(name)
            if name.endswith("/" + PYTHON_BUNDLE):
                # Файл сжат внутри APK, но линкер его не открывает: p4a читает
                # его как архив Python-модулей.
                lines.append(f"  {name}: архив Python (не грузится линкером) — пропущен")
                continue
            try:
                elf_class, aligns = elf_load_alignments(data)
            except ElfError as exc:
                lines.append(f"  {name}: НЕ ЧИТАЕТСЯ ({exc})")
                ok = False
                continue
            worst = min(aligns) if aligns else 0
            bits = 64 if elf_class == 2 else 32
            need_16k = bits == 64
            verdict = "OK 16K" if not need_16k or worst >= MIN_16K_ALIGN else "ТОЛЬКО 4K"
            lines.append(f"  {name}: {bits}-бит, LOAD min={worst:#x} — {verdict}")
            if need_16k and worst < MIN_16K_ALIGN:
                ok = False

        lines.append("")
        lines.append("ИТОГ: " + ("все 64-битные .so совместимы с 16 КБ страницами"
                                if ok else "ЕСТЬ .so без поддержки 16 КБ — на Android 16 "
                                          "такой APK может падать до Java-журнала"))
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("apk", nargs="+", type=Path, help="APK, скачанный из CI")
    args = parser.parse_args(argv)

    all_ok = True
    for path in args.apk:
        ok, lines = check_apk(path)
        all_ok &= ok
        print("\n".join(lines))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
