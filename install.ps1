# Telesthete KVM - Windows Install Script

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "  Telesthete KVM - Software KVM over IP" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host ""

# Check for Python
Write-Host "Checking for Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found!" -ForegroundColor Red
    Write-Host "  Please install Python 3.10+ from https://www.python.org" -ForegroundColor Red
    exit 1
}

# Check Python version
$versionMatch = $pythonVersion -match "Python (\d+)\.(\d+)"
if ($versionMatch) {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
        Write-Host "  ERROR: Python 3.10+ required, found $pythonVersion" -ForegroundColor Red
        exit 1
    }
}

# Check for pip
Write-Host "Checking for pip..." -ForegroundColor Yellow
try {
    $pipVersion = pip --version 2>&1
    Write-Host "  Found: pip" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: pip not found!" -ForegroundColor Red
    exit 1
}

# Install telesthete-kvm
Write-Host ""
Write-Host "Installing Telesthete KVM..." -ForegroundColor Yellow
Write-Host "  This will install:" -ForegroundColor Gray
Write-Host "    - telesthete (transport library)" -ForegroundColor Gray
Write-Host "    - pynput (keyboard/mouse)" -ForegroundColor Gray
Write-Host "    - pyperclip (clipboard)" -ForegroundColor Gray
Write-Host ""

pip install git+https://github.com/Bake-Ware/telesthete-kvm.git

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "ERROR: Installation failed!" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  Installation Complete!" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Create layout.json with your monitor configuration" -ForegroundColor White
Write-Host "     (See example_layout.json)" -ForegroundColor Gray
Write-Host ""
Write-Host "  2. Run on each machine:" -ForegroundColor White
Write-Host "     python -m kvm.kvm --psk ""your-secret"" --hostname HOSTNAME --layout layout.json" -ForegroundColor Gray
Write-Host ""
Write-Host "Documentation: https://github.com/Bake-Ware/telesthete-kvm" -ForegroundColor Cyan
Write-Host ""
