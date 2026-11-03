# hra-do-steamu

Skript, který vezme Windows hru (složku s `.exe` nebo instalátor), připraví ji v **Bottles** nebo **Heroicu** a přidá do **Steamu** jako ne-Steam hru.

## Požadavky
- Python 3 (bez dalších balíčků)
- Bottles nebo Heroic (Flatpak i nativní)
- Steam
- Volitelně: `innoextract` (rozbalení instalátorů bez Wine), `icoutils` (ikona ze `.exe`), `gamemode`

## Použití
```bash
python3 hra-do-steamu.py                      # vybereš spouštěč a hru ze seznamu
python3 hra-do-steamu.py --heroic "/cesta"    # rovnou přes Heroic
python3 hra-do-steamu.py --bottles "/cesta"   # rovnou přes Bottles
```

Nastavení (složky s hrami, Fsync a GameMode pro nové bottle) je nahoře ve skriptu.

## Poznámka
Skript nic nestahuje a nepřihlašuje se do žádného účtu. Pracuje jen s hrami, které už máš na disku. Zálohuje `shortcuts.vdf` Steamu a knihovnu Heroicu před každou změnou.
