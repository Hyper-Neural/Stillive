# Stillive installer

Installs ComfyUI if needed, adds Stillive to it, creates a desktop shortcut, then opens the Stillive preparation page, which downloads the models (about 55 GB).

## Use

Double-click `INSTALLER.bat` (keep it next to the `stillive` folder). It needs Python 3.9 or later; if Python is missing, it shows how to install it. On Linux, or by hand:

    python installateur_stillive.py

- **Express**: one "Install Stillive" button. Default folder: `%USERPROFILE%\Stillive` (`~/Stillive` on Linux).
- **Advanced**: install folder, graphics card type (recent or older), ComfyUI port, do not start at the end.
- If the chosen folder already contains ComfyUI, only Stillive is added.
- Disk space is checked before any download; the installer stops with a clear message if space is missing.
- Without a screen: `--console`, or `--auto` for no questions at all.
- Language: English or French, following the system (`--langue en|fr`).
- If the installation is interrupted, run it again: downloads resume. Log file: `installation.log`.

## What you get in the install folder

- `lancer_stillive.bat` / `.sh`: starts ComfyUI with Stillive (also the desktop shortcut "Stillive").
- `arreter_stillive.bat` / `.sh`: stops it.

## Update

Run the installer again with the new version. Settings, models and clips are kept.

## How Stillive sits in ComfyUI

`custom_nodes/stillive_nodes/` holds the whole `stillive/` folder plus a small `__init__.py` written by the installer. Missing Python packages from `requirements.txt` are installed into ComfyUI's own Python, never the system one.
