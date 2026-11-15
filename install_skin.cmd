@echo off
setlocal
chcp 65001 >nul
pushd "%~dp0"

echo ================================================================
echo   Limbus Company - Rodion (10916) Claude Skin Installer
echo ================================================================
echo.

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto :check_deps

set "SYS_PY="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
    if not errorlevel 1 set "SYS_PY=py -3"
)

if "%SYS_PY%"=="" (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>nul
        if not errorlevel 1 set "SYS_PY=python"
    )
)

if "%SYS_PY%"=="" (
    echo [Error] Python 3.12+ was not found on this PC.
    echo Please install Python 3.12 or newer from https://www.python.org/downloads/
    echo Make sure to check "Add python.exe to PATH" during installation.
    popd
    pause
    exit /b 1
)

echo [1/3] Creating local .venv virtual environment...
%SYS_PY% -m venv "%~dp0.venv"
if errorlevel 1 (
    echo [Error] Failed to create .venv.
    popd
    pause
    exit /b 1
)

:check_deps
"%VENV_PY%" -c "import UnityPy, lz4, PIL, etcpak, astc_encoder, texture2ddecoder" >nul 2>nul
if not errorlevel 1 goto :run_apply

echo [2/3] Installing required packages (UnityPy, lz4, Pillow, texture codecs)...
"%VENV_PY%" -m pip install "UnityPy>=1.25.0" "lz4>=4.4.0" "Pillow>=12.0.0" "etcpak>=0.9.15" "astc-encoder-py>=0.1.12" "texture2ddecoder>=1.0.6"
if errorlevel 1 (
    echo [Info] Default PyPI failed or timed out, retrying with Tuna mirror...
    "%VENV_PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "UnityPy>=1.25.0" "lz4>=4.4.0" "Pillow>=12.0.0" "etcpak>=0.9.15" "astc-encoder-py>=0.1.12" "texture2ddecoder>=1.0.6"
)
if errorlevel 1 (
    echo [Error] Package installation failed. Check your network and try again.
    popd
    pause
    exit /b 1
)

:run_apply
echo [3/3] Backing up original game files and installing skin...
"%VENV_PY%" -X utf8 -m installer apply
set "EC=%ERRORLEVEL%"

echo.
if %EC%==0 (
    echo [SUCCESS] Installation finished! Enjoy Claude-themed Rodion (10916)!
) else (
    echo [FAILED] Installer exited with code %EC%. Please read the message above.
)

popd
pause
exit /b %EC%
