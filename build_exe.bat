@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
rem Keep the release output separate from an existing runtime folder. The
rem application may create a protected cookie file inside dist\BilibiliNotifier.
set "DIST_ROOT=%~dp0dist_release"
set "OUTPUT_DIR=%DIST_ROOT%\BilibiliNotifier"

if not exist "%PYTHON%" (
    echo [ERROR] Project virtual environment was not found:
    echo         "%PYTHON%"
    echo Create it first with: python -m venv .venv
    pause
    exit /b 1
)

if not exist "%~dp0app.ico" (
    echo [ERROR] app.ico was not found in the project folder.
    pause
    exit /b 1
)

"%PYTHON%" -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller is not installed in the project environment. Installing it...
    "%PYTHON%" -m pip install pyinstaller
    if errorlevel 1 (
        echo [ERROR] PyInstaller installation failed.
        pause
        exit /b 1
    )
)

"%PYTHON%" -m pip show PySide6 >nul 2>&1
if errorlevel 1 (
    echo [ERROR] PySide6 is not installed in the project environment.
    echo Install project dependencies first:
    echo         "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)

echo Building BilibiliNotifier...
"%PYTHON%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --distpath "%DIST_ROOT%" ^
    --icon "%~dp0app.ico" ^
    --add-data "%~dp0app.ico;." ^
    --name BilibiliNotifier ^
    --collect-all PySide6 ^
    "%~dp0bilibili_followed_dynamics.py"

if errorlevel 1 (
    echo [ERROR] Build failed. Review the messages above.
    pause
    exit /b 1
)

if exist "%~dp0config.json" (
    copy /Y "%~dp0config.json" "%OUTPUT_DIR%\config.json" >nul
    if errorlevel 1 (
        echo [ERROR] Build succeeded, but config.json could not be copied.
        pause
        exit /b 1
    )
)

echo.
echo Build completed:
echo "%OUTPUT_DIR%\BilibiliNotifier.exe"
echo.
echo Distribute the entire BilibiliNotifier folder, not just the EXE.
echo Login cookies and dynamic history are not included in the build.
pause
exit /b 0
