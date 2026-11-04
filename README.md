# steam-wine-setup

A script that takes a Windows game (a folder with an `.exe`, or an installer `setup.exe`), sets it up in **Bottles** or **Heroic**, and adds it to **Steam** as a non-Steam game.

## Requirements
- Python 3 (no extra packages)
- Bottles or Heroic (Flatpak or native)
- Steam
- Optional: `innoextract` (unpacks Inno Setup installers without Wine), `icoutils` (icon from the `.exe`), `gamemode`

## Usage
```bash
python3 steam-wine-setup.py                      # pick the launcher and the game from a list
python3 steam-wine-setup.py --heroic "/path"     # straight to Heroic
python3 steam-wine-setup.py --bottles "/path"    # straight to Bottles
```

Settings (game folders, Fsync and GameMode for new bottles) are at the top of the script.

## Note
The script does not download anything and does not log in to any account. It only works with games already on your disk. It backs up Steam's `shortcuts.vdf` and the Heroic library before every change.
