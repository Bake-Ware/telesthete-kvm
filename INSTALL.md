# Installation Guide

## Windows

### Option 1: One-Click Install (Recommended)

Double-click `install.bat` or run in PowerShell:

```powershell
.\install.ps1
```

This will:
- Check for Python 3.10+
- Install telesthete-kvm and all dependencies
- Show next steps

### Option 2: Manual Install

```powershell
pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

## Linux

```bash
pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

## macOS

```bash
pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

*Note: macOS support is experimental and untested.*

## Requirements

- Python 3.10 or higher
- pip (usually included with Python)
- Internet connection (for downloading packages)

## Verify Installation

```bash
python -m kvm.kvm --help
```

You should see the help message with available options.

## Troubleshooting

**"Python not found"**
- Install Python from https://www.python.org
- Make sure to check "Add Python to PATH" during installation

**"pip not found"**
- Python should include pip by default
- Try: `python -m ensurepip`

**"git not found" error from pip**
- Install Git from https://git-scm.com
- Or use: `pip install https://github.com/Bake-Ware/telesthete-kvm/archive/main.zip`

**Permission errors**
- On Windows: Run as Administrator
- On Linux: Use `--user` flag: `pip install --user git+https://...`

**pynput doesn't work**
- On Linux with Wayland: May need X11 or special permissions
- On Windows: No admin needed for most cases

## Next Steps

After installation:

1. Create `layout.json` (see `example_layout.json`)
2. Run on each machine with the same PSK and layout
3. See README.md for detailed usage

## Uninstall

```bash
pip uninstall telesthete-kvm telesthete
```
