@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 install.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw install.py & exit /b)
where python >nul 2>nul && (python install.py & exit /b)
echo Не найден Python.
echo Скачай его на https://www.python.org/downloads/ и при установке поставь галочку "Add python.exe to PATH".
echo Потом запусти этот файл ещё раз.
pause
