@echo off
REM Telesthete KVM - Windows Install Script (Batch version)

echo ==================================================
echo   Telesthete KVM - Software KVM over IP
echo ==================================================
echo.

REM Check for Python
echo Checking for Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: Python not found!
    echo   Please install Python 3.10+ from https://www.python.org
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
echo   Found: %PYTHON_VERSION%

REM Check for pip
echo Checking for pip...
pip --version >nul 2>&1
if errorlevel 1 (
    echo   ERROR: pip not found!
    pause
    exit /b 1
)
echo   Found: pip

REM Install
echo.
echo Installing Telesthete KVM...
echo   This will install:
echo     - telesthete (transport library)
echo     - pynput (keyboard/mouse)
echo     - pyperclip (clipboard)
echo.

pip install git+https://github.com/Bake-Ware/telesthete-kvm.git

if errorlevel 1 (
    echo.
    echo ERROR: Installation failed!
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   Installation Complete!
echo ==================================================
echo.
echo Next steps:
echo   1. Create layout.json with your monitor configuration
echo      (See example_layout.json)
echo.
echo   2. Run on each machine:
echo      python -m kvm.kvm --psk "your-secret" --hostname HOSTNAME --layout layout.json
echo.
echo Documentation: https://github.com/Bake-Ware/telesthete-kvm
echo.
pause
