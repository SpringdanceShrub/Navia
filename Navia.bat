@echo off
setlocal EnableExtensions DisableDelayedExpansion
title Navia

pushd "%~dp0"
if errorlevel 1 goto directory_error
if not exist "backend\launcher.py" goto package_error

if exist ".venv\Scripts\python.exe" goto use_venv
where py >nul 2>&1
if not errorlevel 1 goto use_launcher
where python >nul 2>&1
if errorlevel 1 goto python_error

python -B -m backend.launcher %*
goto finished

:use_launcher
py -3 -B -m backend.launcher %*
goto finished

:use_venv
".venv\Scripts\python.exe" -B -m backend.launcher %*
goto finished

:finished
set "NAVIA_EXIT_CODE=%errorlevel%"
if "%NAVIA_EXIT_CODE%"=="0" goto close
echo.
echo Navia no pudo iniciarse. Revisa el mensaje anterior.
pause
goto close

:python_error
echo.
echo No se encontro Python. Instala Python 3.12 o posterior para Windows.
echo Descarguelo desde https://www.python.org/downloads/windows/
echo Despues vuelva a ejecutar este archivo.
set "NAVIA_EXIT_CODE=1"
pause
goto close

:package_error
echo.
echo Faltan archivos de Navia. Extrae el ZIP completo antes de ejecutarlo.
set "NAVIA_EXIT_CODE=1"
pause
goto close

:directory_error
echo No se pudo abrir la carpeta de Navia.
pause
endlocal
exit /b 1

:close
popd
endlocal & exit /b %NAVIA_EXIT_CODE%
