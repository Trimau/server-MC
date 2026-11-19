@echo off
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m pip install -r requirements.txt "pyinstaller>=6.9" || goto :error
%PY% -m PyInstaller --noconfirm --clean --onefile --windowed --name launcher --add-data "modpack.zip;." --hidden-import PIL._tkinter_finder launcher.py || goto :error
echo.
echo Listo: dist\launcher.exe
pause
exit /b 0
:error
echo.
echo Algo fallo. Revisa los mensajes de arriba.
pause
exit /b 1
