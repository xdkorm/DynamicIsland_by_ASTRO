"""Установщик «Dynamic Island by ASTRO».
Что делает: копирует программу в папку пользователя, создаёт для неё свою среду Python
(.venv), ставит нужные библиотеки, делает ярлыки (рабочий стол и меню «Пуск») с вашей иконкой
и добавляет запись в «Установленные приложения» Windows — оттуда её можно удалить.
Запуск: двойной клик по «Установить.bat».
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import venv
from pathlib import Path

from exe_branding import make_runner

APP = "Dynamic Island by ASTRO"
SHORT = "Astro Island"             # имя ярлыков
FOLDER = "DynamicIslandByASTRO"
RUN_NAME = "DynamicIslandRU"        # тот же ключ автозапуска, что использует переключатель в настройках
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DynamicIslandByASTRO"
HERE = Path(__file__).resolve().parent
SRC = HERE / "app"
DEFAULT_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Programs" / FOLDER
NOWIN = 0x08000000  # CREATE_NO_WINDOW

WINRT = ["winrt-runtime", "winrt-Windows.Foundation", "winrt-Windows.Foundation.Collections",
         "winrt-Windows.Media.Control", "winrt-Windows.Storage.Streams"]
# (что даёт, пакеты) — обязательное первым; остальное необязательное: не поставилось — вкладка подскажет сама
PACKAGES = [
    ("основа (PySide6)", ["PySide6"], True),
    ("система и микшер", ["psutil", "pycaw", "comtypes"], False),
    ("скриншоты", ["pillow"], False),
    ("запись экрана", ["imageio-ffmpeg", "PyAudioWPatch"], False),
    ("музыка и тексты песен", WINRT if sys.version_info >= (3, 13) else ["winsdk"], False),
]


def ps_quote(s) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def run_ps(script: str) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, creationflags=NOWIN)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "PowerShell завершился с ошибкой")
    return r.stdout.strip()


def make_shortcut(lnk: Path, target: Path, args: str, workdir: Path, icon: Path, desc: str):
    lnk.parent.mkdir(parents=True, exist_ok=True)
    run_ps(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut(%s);"
        "$s.TargetPath=%s;$s.Arguments=%s;$s.WorkingDirectory=%s;$s.IconLocation=%s;$s.Description=%s;"
        "$s.WindowStyle=7;$s.Save()" % (ps_quote(lnk), ps_quote(target), ps_quote(args),
                                         ps_quote(workdir), ps_quote(str(icon) + ",0"), ps_quote(desc)))


def stop_running(install_dir: Path):
    """Закрыть уже запущенный остров из этой папки, чтобы файлы можно было заменить."""
    try:
        run_ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like %s } | "
               "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
               % ps_quote("*" + str(install_dir) + "*"))
    except Exception:
        pass


def install(opts, log, step):
    import winreg
    d = Path(opts["dir"])
    pyw = d / ".venv" / "Scripts" / "pythonw.exe"
    py = d / ".venv" / "Scripts" / "python.exe"

    step(5, "Закрываю запущенный остров…")
    stop_running(d)

    step(12, "Копирую файлы…")
    d.mkdir(parents=True, exist_ok=True)
    for f in SRC.iterdir():
        if f.is_file():
            shutil.copy2(f, d / f.name)
    shutil.copy2(HERE / "uninstall.py", d / "uninstall.py")
    log(f"Файлы скопированы в {d}")

    step(22, "Создаю среду Python (один раз, пара минут)…")
    if not py.exists():
        venv.create(d / ".venv", with_pip=True)
    log("Среда Python готова")

    base, span = 30, 50
    for i, (title, pkgs, required) in enumerate(PACKAGES):
        step(base + span * i // len(PACKAGES), f"Ставлю: {title}…")
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", *pkgs]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN)
        if r.returncode == 0:
            log(f"✓ {title}")
        elif required:
            raise RuntimeError(f"Не получилось поставить {title}:\n{(r.stderr or r.stdout)[-600:]}")
        else:  # по одному, чтобы одна неудача не тянула остальные
            ok = []
            for p in pkgs:
                rr = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", p],
                                    capture_output=True, text=True, creationflags=NOWIN)
                ok.append(rr.returncode == 0)
            log(("✓ " if all(ok) else "⚠ частично не поставилось: ") + title)

    step(80, "Делаю имя «Astro Island» для Диспетчера задач…")
    scripts = d / ".venv" / "Scripts"
    island_exe = make_runner(scripts, "AstroIsland.exe", "Astro Island") or pyw
    settings_exe = make_runner(scripts, "AstroIslandSettings.exe", "Astro Island — настройки") or pyw
    log("✓ процесс называется «Astro Island»" if island_exe != pyw
        else "⚠ имя процесса не изменилось (будет «Python») — остров работает как обычно")

    step(84, "Создаю ярлыки…")
    icon = d / "icon.ico"
    info = {"desktop": "", "start_dir": "", "autostart": bool(opts["autostart"])}
    if opts["desktop"]:
        desktop = Path(run_ps("[Environment]::GetFolderPath('Desktop')"))
        lnk = desktop / f"{SHORT}.lnk"
        make_shortcut(lnk, island_exe, f'"{d / "island.py"}"', d, icon, APP)
        info["desktop"] = str(lnk)
        log("✓ ярлык на рабочем столе")
    if opts["start"]:
        sd = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORT
        make_shortcut(sd / f"{SHORT}.lnk", island_exe, f'"{d / "island.py"}"', d, icon, APP)
        make_shortcut(sd / "Настройки.lnk", settings_exe, f'"{d / "settings_app.py"}"', d, icon, f"{APP} — настройки")
        make_shortcut(sd / "Удалить.lnk", pyw, f'"{d / "uninstall.py"}"', d, icon, f"Удалить {APP}")
        info["start_dir"] = str(sd)
        log("✓ ярлыки в меню «Пуск»")
    (d / "install.json").write_text(json.dumps(info, ensure_ascii=False), "utf-8")

    step(92, "Регистрирую в Windows…")
    if opts["autostart"]:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{island_exe}" "{d / "island.py"}"')
        log("✓ автозапуск с Windows")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
        for name, val in (("DisplayName", APP), ("DisplayVersion", "1.0"), ("Publisher", "ASTRO"),
                          ("DisplayIcon", str(icon)), ("InstallLocation", str(d)),
                          ("UninstallString", f'"{pyw}" "{d / "uninstall.py"}"')):
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, val)
        winreg.SetValueEx(k, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(k, "NoRepair", 0, winreg.REG_DWORD, 1)
    log("✓ запись в «Установленных приложениях»")

    if opts["launch"]:
        step(98, "Запускаю…")
        subprocess.Popen([str(island_exe), str(d / "island.py")], cwd=str(d), creationflags=0x00000008 | NOWIN)
    step(100, "Готово!")


def main():
    if os.name != "nt":
        print("Установщик рассчитан на Windows.")
        return 1
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    BG, FG, MUTED, CARD, ACC = "#141418", "#f2f2f7", "#8e8e93", "#1d1d23", "#8b7cf6"
    root = tk.Tk()
    root.title(f"Установка · {APP}")
    root.configure(bg=BG)
    root.geometry("560x640")
    root.resizable(False, False)
    try:
        root.iconbitmap(str(SRC / "icon.ico"))
    except Exception:
        pass

    logo = None
    try:
        logo = tk.PhotoImage(file=str(HERE / "icon_128.png"))
        tk.Label(root, image=logo, bg=BG).pack(pady=(18, 4))
    except Exception:
        pass
    tk.Label(root, text=APP, bg=BG, fg=FG, font=("Segoe UI", 18, "bold")).pack()
    tk.Label(root, text="Установка займёт пару минут: нужно скачать библиотеки.", bg=BG, fg=MUTED,
             font=("Segoe UI", 10)).pack(pady=(2, 10))

    box = tk.Frame(root, bg=CARD)
    box.pack(fill="x", padx=24)
    tk.Label(box, text="Папка установки", bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(10, 0))
    row = tk.Frame(box, bg=CARD)
    row.pack(fill="x", padx=14, pady=(2, 8))
    dir_var = tk.StringVar(value=str(DEFAULT_DIR))
    tk.Entry(row, textvariable=dir_var, bg="#25252d", fg=FG, insertbackground=FG, relief="flat",
             font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True, ipady=5)

    def browse():
        p = filedialog.askdirectory(initialdir=dir_var.get())
        if p:
            dir_var.set(str(Path(p) / FOLDER))
    tk.Button(row, text="Обзор…", command=browse, bg="#3a3a44", fg=FG, relief="flat", padx=10,
              activebackground="#4a4a55", activeforeground=FG).pack(side="left", padx=(8, 0))

    v_desktop, v_start = tk.BooleanVar(value=True), tk.BooleanVar(value=True)
    v_auto, v_launch = tk.BooleanVar(value=False), tk.BooleanVar(value=True)
    for text, var in (("Ярлык на рабочем столе", v_desktop), ("Ярлыки в меню «Пуск»", v_start),
                      ("Запускать вместе с Windows", v_auto), ("Запустить после установки", v_launch)):
        tk.Checkbutton(box, text=text, variable=var, bg=CARD, fg=FG, selectcolor="#25252d",
                       activebackground=CARD, activeforeground=FG, font=("Segoe UI", 10)).pack(anchor="w", padx=10)
    tk.Frame(box, height=8, bg=CARD).pack()

    status = tk.Label(root, text="", bg=BG, fg=FG, font=("Segoe UI", 10))
    status.pack(pady=(12, 2))
    style = ttk.Style()
    style.theme_use("clam")
    style.configure("A.Horizontal.TProgressbar", troughcolor="#25252d", background=ACC, bordercolor=BG,
                    lightcolor=ACC, darkcolor=ACC)
    bar = ttk.Progressbar(root, style="A.Horizontal.TProgressbar", maximum=100, length=512)
    bar.pack()
    logbox = tk.Text(root, height=8, bg=CARD, fg=MUTED, relief="flat", font=("Consolas", 9), state="disabled")
    logbox.pack(fill="both", padx=24, pady=10, expand=True)
    btn = tk.Button(root, text="Установить", bg=ACC, fg="#ffffff", relief="flat", font=("Segoe UI", 11, "bold"),
                    padx=24, pady=8, activebackground="#9d90f8", activeforeground="#ffffff")
    btn.pack(pady=(0, 16))

    q = queue.Queue()

    def pump():
        try:
            while True:
                kind, a, b = q.get_nowait()
                if kind == "log":
                    logbox.config(state="normal")
                    logbox.insert("end", a + "\n")
                    logbox.see("end")
                    logbox.config(state="disabled")
                elif kind == "step":
                    bar["value"] = a
                    status.config(text=b)
                elif kind == "done":
                    btn.config(state="normal")
                    if a:
                        btn.config(text="Закрыть", command=root.destroy)
                        messagebox.showinfo(APP, "Готово! Остров установлен.\nЯрлык ищи на рабочем столе или в меню «Пуск».")
                    else:
                        btn.config(text="Повторить")
                        messagebox.showerror(APP, f"Не получилось установить:\n\n{b}")
        except queue.Empty:
            pass
        root.after(100, pump)

    def work(opts):
        try:
            install(opts, lambda m: q.put(("log", m, None)), lambda p, t: q.put(("step", p, t)))
            q.put(("done", True, None))
        except Exception as e:
            q.put(("log", f"Ошибка: {e}", None))
            q.put(("done", False, str(e)))

    def start():
        btn.config(state="disabled")
        opts = {"dir": dir_var.get().strip() or str(DEFAULT_DIR), "desktop": v_desktop.get(),
                "start": v_start.get(), "autostart": v_auto.get(), "launch": v_launch.get()}
        threading.Thread(target=work, args=(opts,), daemon=True).start()

    btn.config(command=start)
    pump()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
