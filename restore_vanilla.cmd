@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

echo ================================================================
echo   Limbus Company - Restore Vanilla Files (Rodion 10916 Skin)
echo ================================================================
echo.

set "RUN_PY="
if exist "%~dp0.venv\Scripts\python.exe" set "RUN_PY=%~dp0.venv\Scripts\python.exe"

if "%RUN_PY%"=="" (
    where py >nul 2>nul
    if %errorlevel%==0 set "RUN_PY=py -3"
)

if "%RUN_PY%"=="" (
    where python >nul 2>nul
    if %errorlevel%==0 set "RUN_PY=python"
)

if "%RUN_PY%"=="" (
    echo [Error] No Python runtime found to execute restore.
    popd
    pause
    exit /b 1
)

"%RUN_PY%" -X utf8 -m installer revert
set "EC=%ERRORLEVEL%"

echo.
if %EC%==0 (
    echo [SUCCESS] All backed up game files have been restored to vanilla state.
) else (
    echo [FAILED] Restore exited with code %EC%. Please read the message above.
)

popd
pause
exit /b %EC%
