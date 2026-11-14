"""Свои имена процессов для Диспетчера задач: копия pythonw.exe с новым именем и описанием
(версия-ресурс exe переписывается через WinAPI). Нужен только установщику."""
import ctypes
import shutil
import struct
import subprocess
import sys
from pathlib import Path


def _pad(b: bytes) -> bytes:
    return b + b"\0" * ((-len(b)) % 4)


def _block(key: str, value: bytes = b"", vlen: int = 0, typ: int = 1, children=()) -> bytes:
    s = struct.pack("<HHH", 0, vlen, typ) + (key + "\0").encode("utf-16-le")
    s = _pad(s) + value
    if children:
        s = _pad(s) + b"".join(_pad(c) for c in children)
    return struct.pack("<H", len(s)) + s[2:]


def build_version_info(strings: dict, version=(1, 0, 0, 0)) -> bytes:
    ms, ls = (version[0] << 16) | version[1], (version[2] << 16) | version[3]
    fixed = struct.pack("<13I", 0xFEEF04BD, 0x00010000, ms, ls, ms, ls, 0x3F, 0, 0x00040004, 1, 0, 0, 0)
    entries = [_block(k, (v + "\0").encode("utf-16-le"), len(v) + 1, 1) for k, v in strings.items()]
    table = _block("040904B0", children=entries)
    sfi = _block("StringFileInfo", children=[table])
    var = _block("Translation", struct.pack("<HH", 0x0409, 0x04B0), 4, 0)
    vfi = _block("VarFileInfo", children=[var])
    return _block("VS_VERSION_INFO", fixed, len(fixed), 0, [sfi, vfi])


def set_version_info(exe: Path, description: str, product: str, company: str = "ASTRO"):
    from ctypes import wintypes
    k32 = ctypes.windll.kernel32
    k32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
    k32.BeginUpdateResourceW.restype = wintypes.HANDLE
    k32.UpdateResourceW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD,
                                    ctypes.c_void_p, wintypes.DWORD]
    k32.UpdateResourceW.restype = wintypes.BOOL
    k32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
    k32.EndUpdateResourceW.restype = wintypes.BOOL
    strings = {"CompanyName": company, "FileDescription": description, "FileVersion": "1.0.0.0",
               "InternalName": exe.stem, "OriginalFilename": exe.name, "ProductName": product,
               "ProductVersion": "1.0.0.0"}
    data = build_version_info(strings)
    buf = ctypes.create_string_buffer(data, len(data))
    h = k32.BeginUpdateResourceW(str(exe), False)
    if not h:
        raise OSError("BeginUpdateResource: " + str(ctypes.WinError()))
    ok = k32.UpdateResourceW(h, 16, 1, 0x0409, buf, len(data))   # RT_VERSION, id 1, en-US
    k32.EndUpdateResourceW(h, not ok)
    if not ok:
        raise OSError("UpdateResource: " + str(ctypes.WinError()))


def make_runner(scripts: Path, name: str, description: str, product: str = "Astro Island"):
    """Копия pythonw.exe под именем name (рядом нужные dll). Возвращает путь или None, если не вышло."""
    base = Path(sys.base_prefix)
    src = base / "pythonw.exe"
    dst = scripts / name
    try:
        dst.unlink(missing_ok=True)
        shutil.copy2(src, dst)
        for pat in ("python3*.dll", "vcruntime*.dll"):
            for dll in base.glob(pat):
                if not (scripts / dll.name).exists():
                    shutil.copy2(dll, scripts / dll.name)
        set_version_info(dst, description, product)
        r = subprocess.run([str(dst), "-c", "import PySide6"], creationflags=0x08000000, timeout=90)
        if r.returncode != 0:
            raise RuntimeError("копия Python не запустилась")
        return dst
    except Exception as e:
        print("Свой exe не получился:", e)
        try:
            dst.unlink(missing_ok=True)
        except Exception:
            pass
        return None
