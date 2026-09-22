$ErrorActionPreference = "Stop"
$venv = Join-Path $env:USERPROFILE ".telesthete-kvm"
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Python 3.11 or newer is required" }
git --version
if ($LASTEXITCODE -ne 0) { throw "Git is required" }
python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "Could not create virtual environment" }
$python = Join-Path $venv "Scripts\python.exe"
& $python -m pip install --upgrade git+https://github.com/Bake-Ware/telesthete-kvm.git
if ($LASTEXITCODE -ne 0) { throw "Installation failed" }
& $python -m kvm --help
if ($LASTEXITCODE -ne 0) { throw "CLI verification failed" }
Write-Host "Installed. Command: $venv\Scripts\telesthete-kvm.exe"
Write-Host "Create layout.json and kvm.psk, then pass --hostname, --layout, and --psk-file."
