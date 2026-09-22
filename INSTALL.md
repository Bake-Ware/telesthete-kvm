# Installation

## Linux X11

On Debian/Ubuntu:

```sh
sudo apt install python3-venv python3-tk git xclip
python3 -m venv ~/.venvs/telesthete-kvm
~/.venvs/telesthete-kvm/bin/python -m pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

Run `~/.venvs/telesthete-kvm/bin/telesthete-kvm` inside your graphical X11 session.
Do not run as root for normal desktop use. `DISPLAY` and X11 authentication must
refer to the desktop you want to share. A native Wayland session is unsupported;
choose an X11 session at login. XWayland alone does not expose the entire desktop.

## Windows

Install Python 3.10+ and Git, then run `install.bat` or `install.ps1`. The scripts
create a virtual environment under your user profile and print the command path.
No system Python packages are changed. Alternatively:

```powershell
python -m venv "$env:USERPROFILE\.telesthete-kvm"
& "$env:USERPROFILE\.telesthete-kvm\Scripts\python.exe" -m pip install git+https://github.com/Bake-Ware/telesthete-kvm.git
```

Run in the interactive desktop session. Elevated windows and the Windows secure
desktop may reject input from a non-elevated process. macOS is not supported.

## From this checkout

```sh
python -m venv .venv
# Activate the environment, then:
python -m pip install -e '.[test]'
python -m pytest -q
```

The dependency metadata installs Telesthete from a pinned Git revision. Git and
network access are required. All peers must use the same application version.

## Verify and configure

```sh
telesthete-kvm --help
telesthete-kvm --psk-file kvm.psk --hostname desktop --layout layout.json --check
```

Follow [README.md](README.md) for layout, secrets, LAN discovery, and hub mode.
`--help` and `--check` work without an active desktop. They do not verify desktop
capture permissions or connectivity.

To uninstall, delete the virtual environment after stopping the application.
