#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
install-from-disk.py
====================
Nainstaluje Windows hru z instalátoru (setup.exe) přímo z externího disku.
Instalace jde do Wine prefixu na interním disku a hra se přidá do Heroicu.
Samotný instalátor se nikam nekopíruje ani nerozbaluje.

Použití:
    python3 install-from-disk.py "/cesta/k/setup.exe"
    python3 install-from-disk.py "/cesta/k/setup.exe" "Název hry"
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time

HEROIC = os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic")
UMU = os.path.join(HEROIC, "tools/runtimes/umu/umu_run.py")
PROTON = os.path.join(HEROIC, "tools/proton/GE-Proton-latest")
LIBRARY = os.path.join(HEROIC, "sideload_apps/library.json")
GAMES_CONFIG = os.path.join(HEROIC, "GamesConfig")
PREFIXY = os.path.expanduser("~/Games/Heroic/Prefixes")

# Soubory, které nejsou hra (instalátory, redistribuce, deinstalátory)
NE_HRY = re.compile(r"unins|setup|install|redist|vc_|vcredist|dxsetup|crash|helper|launcher", re.I)


def ask(otazka, vychozi=None):
    suffix = f" [{vychozi}]" if vychozi else ""
    odpoved = input(f"{otazka}{suffix}: ").strip()
    return odpoved or vychozi or ""


def heroic_bezi():
    return subprocess.run(["pgrep", "-f", "/app/bin/heroic/heroic$"], capture_output=True).returncode == 0


def nove_exe(drive_c, od_casu):
    nalezene = []
    for koren, _, soubory in os.walk(drive_c):
        for s in soubory:
            if not s.lower().endswith(".exe") or NE_HRY.search(s):
                continue
            cesta = os.path.join(koren, s)
            try:
                if os.path.getmtime(cesta) >= od_casu:
                    nalezene.append(cesta)
            except OSError:
                pass
    return sorted(nalezene)


def zapis_do_heroicu(nazev, prefix, exe):
    # Heroic musí být zavřený, jinak by si knihovnu přepsal zpátky
    app_name = "%016x" % random.getrandbits(64)
    slozka = os.path.dirname(exe)

    if os.path.exists(LIBRARY):
        shutil.copy2(LIBRARY, LIBRARY + f".pred-pridanim-{time.strftime('%Y%m%d-%H%M%S')}")
        with open(LIBRARY, encoding="utf-8") as f:
            knihovna = json.load(f)
    else:
        knihovna = {"games": []}

    knihovna["games"].append({
        "runner": "sideload",
        "app_name": app_name,
        "title": nazev,
        "install": {"executable": exe, "platform": "Windows", "is_dlc": False},
        "folder_name": slozka,
        "art_cover": "",
        "art_square": "",
        "is_installed": True,
        "canRunOffline": True,
    })
    with open(LIBRARY, "w", encoding="utf-8") as f:
        json.dump(knihovna, f, indent=2, ensure_ascii=False)

    os.makedirs(GAMES_CONFIG, exist_ok=True)
    nastaveni = {
        app_name: {
            "winePrefix": prefix,
            "wineVersion": {
                "bin": os.path.join(PROTON, "proton"),
                "name": os.path.basename(PROTON),
                "type": "proton",
            },
        },
        "version": "v0",
        "explicit": True,
    }
    with open(os.path.join(GAMES_CONFIG, f"{app_name}.json"), "w", encoding="utf-8") as f:
        json.dump(nastaveni, f, indent=2, ensure_ascii=False)
    return app_name


def main():
    if len(sys.argv) < 2:
        sys.exit("Použití: install-from-disk.py \"/cesta/k/setup.exe\" [název hry]")

    instalator = os.path.realpath(os.path.expanduser(sys.argv[1]))
    if not os.path.isfile(instalator):
        sys.exit(f"Soubor {instalator} neexistuje.")
    if not os.path.isfile(UMU) or not os.path.isdir(PROTON):
        sys.exit("Nenašel jsem Heroic nástroje (umu, GE-Proton). Spusť Heroic a nainstaluj GE-Proton.")
    if heroic_bezi():
        sys.exit("Heroic běží. Zavři ho a spusť skript znovu.")

    nazev = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(os.path.dirname(instalator))
    nazev = ask("Název hry", nazev)
    slozka_prefixu = re.sub(r"[^\w\- ]", "", nazev).strip() or "hra"
    prefix = os.path.join(PREFIXY, slozka_prefixu)
    if os.path.exists(prefix):
        sys.exit(f"Prefix {prefix} už existuje. Smaž ho nebo zvol jiný název.")

    print(f"\nInstalátor: {instalator}")
    print(f"Prefix: {prefix}")
    os.makedirs(prefix)
    env = dict(os.environ, WINEPREFIX=prefix, PROTONPATH=PROTON, GAMEID="umu-default",
               STEAM_COMPAT_DATA_PATH=prefix)

    od_casu = time.time() - 2
    proces = subprocess.Popen([sys.executable, UMU, instalator], env=env,
                              cwd=os.path.dirname(instalator))
    print("\nProjdi instalaci ve Wine okně. Cestu nech výchozí (C:\\...).")
    input("Až instalace skončí a okno instalátoru zavřeš, stiskni Enter... ")
    if proces.poll() is None:
        print("Instalátor ještě běží, počkám na něj.")
        proces.wait()

    drive_c = os.path.join(prefix, "drive_c")
    nalezene = nove_exe(drive_c, od_casu)
    if not nalezene:
        sys.exit("Žádné nově nainstalované .exe jsem nenašel. Prefix zůstal, můžeš to zkusit znovu.")

    print("\nNově nainstalované .exe:")
    for i, cesta in enumerate(nalezene, 1):
        print(f"  {i}. {os.path.relpath(cesta, drive_c)}")
    volba = ask("Které .exe spouští hru (číslo)", "1")
    try:
        exe = nalezene[int(volba) - 1]
    except (ValueError, IndexError):
        sys.exit("Neplatná volba.")

    if heroic_bezi():
        sys.exit("Heroic je teď spuštěný, zavři ho a spusť skript znovu, ať se hra zapíše.")
    app_name = zapis_do_heroicu(nazev, prefix, exe)
    print(f"\nHotovo. Hra „{nazev}“ je v Heroicu (ID {app_name}).")
    print("Heroic spusť znovu a najdeš ji v knihovně.")


if __name__ == "__main__":
    main()
