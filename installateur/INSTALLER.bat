@echo off
rem Lance l'installateur Stillive : cherche Python 3.9 ou plus, sinon explique quoi faire.
cd /d "%~dp0"
set "TEST=import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)"

py -3 -c "%TEST%" >nul 2>&1
if not errorlevel 1 (
    py -3 installateur_stillive.py %*
    goto :fin
)
python -c "%TEST%" >nul 2>&1
if not errorlevel 1 (
    python installateur_stillive.py %*
    goto :fin
)

echo.
echo Stillive a besoin de Python (version 3.9 ou plus) pour s'installer, et il est absent de cet ordinateur.
echo.
echo Pour l'installer, ouvre une invite de commandes et tape :
echo     winget install Python.Python.3.13
echo ou telecharge-le sur https://www.python.org/downloads/ (coche "Add python.exe to PATH").
echo.
echo Ensuite, double-clique de nouveau sur INSTALLER.bat.
echo.
echo Stillive needs Python 3.9 or newer to install, and it was not found on this computer.
echo Install it with:  winget install Python.Python.3.13
echo or from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run INSTALLER.bat again.
echo.
pause
exit /b 1

:fin
if errorlevel 1 pause
