@echo off
setlocal
set "KVM_ENV=%USERPROFILE%\.telesthete-kvm"
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto fail
git --version >nul 2>&1
if errorlevel 1 goto fail
python -m venv "%KVM_ENV%"
if errorlevel 1 goto fail
"%KVM_ENV%\Scripts\python.exe" -m pip install --upgrade git+https://github.com/Bake-Ware/telesthete-kvm.git
if errorlevel 1 goto fail
"%KVM_ENV%\Scripts\python.exe" -m kvm --help
if errorlevel 1 goto fail
echo Installed. Command: "%KVM_ENV%\Scripts\telesthete-kvm.exe"
echo Create layout.json and kvm.psk, then pass --hostname, --layout, and --psk-file.
exit /b 0
:fail
echo Installation failed. Python 3.10+, Git, and network access are required.
exit /b 1
