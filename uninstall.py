"""Удаление «Dynamic Island by ASTRO»: ярлыки, автозапуск, запись в Windows и сама папка."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = "Dynamic Island by ASTRO"
RUN_NAME = "DynamicIslandRU"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DynamicIslandByASTRO"
HERE = Path(__file__).resolve().parent
NOWIN = 0x08000000


def main():
    import tkinter as tk
    from tkinter import messagebox
    import winreg

    root = tk.Tk()
    root.withdraw()
    try:
        root.iconbitmap(str(HERE / "icon.ico"))
    except Exception:
        pass
    if not messagebox.askyesno(APP, f"Удалить {APP} с этого компьютера?"):
        return 0
    wipe_settings = messagebox.askyesno(
        APP, "Удалить и настройки (история буфера, полка, напоминания)?\n\n«Нет» — они останутся на случай переустановки.")

    # закрыть остров и настройки
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' -and $_.ProcessId -ne %d } | "
                        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
                        % (str(HERE).replace("'", "''"), os.getpid())], creationflags=NOWIN)
    except Exception:
        pass

    info = {}
    try:
        info = json.loads((HERE / "install.json").read_text("utf-8"))
    except Exception:
        pass
    if info.get("desktop"):
        Path(info["desktop"]).unlink(missing_ok=True)
    if info.get("start_dir"):
        shutil.rmtree(info["start_dir"], ignore_errors=True)

    for path, name in ((r"Software\Microsoft\Windows\CurrentVersion\Run", RUN_NAME),):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, name)
        except OSError:
            pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    if wipe_settings:
        cfg = Path(os.environ.get("APPDATA", str(Path.home()))) / "DynamicIslandRU"
        shutil.rmtree(cfg, ignore_errors=True)

    # папку нельзя стереть, пока из неё идёт этот процесс — удалим сразу после выхода
    subprocess.Popen(f'cmd /c ping 127.0.0.1 -n 3 >nul & rmdir /s /q "{HERE}"', shell=True,
                     creationflags=0x00000008 | NOWIN)
    messagebox.showinfo(APP, "Удалено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
