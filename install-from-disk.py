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
import urllib.parse

HEROIC = os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config/heroic")
UMU = os.path.join(HEROIC, "tools/runtimes/umu/umu_run.py")
PROTON = os.path.join(HEROIC, "tools/proton/GE-Proton-latest")
LIBRARY = os.path.join(HEROIC, "sideload_apps/library.json")
GAMES_CONFIG = os.path.join(HEROIC, "GamesConfig")
PREFIXY = os.path.expanduser("~/Games/Heroic/Prefixes")

# Soubory, které nejsou hra (instalátory, redistribuce, deinstalátory)
NE_HRY = re.compile(r"unins|setup|install|redist|vc_|vcredist|dxsetup|dxwebsetup|crash|helper|handler|crs-|"
                    r"launcher|prereq|dotnet|oalinst|report|unitycrash|steamcmd|vulkan|ue4", re.I)


def cesta_z_argumentu(text):
    """Převede cestu nebo file:// adresu (z přetažení) na skutečnou cestu k setup.exe."""
    text = text.strip()
    if text.startswith("file://"):
        text = urllib.parse.unquote(urllib.parse.urlparse(text).path)
    cesta = os.path.realpath(os.path.expanduser(text))
    if os.path.isdir(cesta):
        for koren, _, soubory in os.walk(cesta):
            for s in soubory:
                if s.lower() == "setup.exe":
                    return os.path.join(koren, s)
    return cesta


def ask(otazka, vychozi=None):
    suffix = f" [{vychozi}]" if vychozi else ""
    odpoved = input(f"{otazka}{suffix}: ").strip()
    return odpoved or vychozi or ""


def heroic_bezi():
    return subprocess.run(["pgrep", "-f", "/app/bin/heroic/heroic$"], capture_output=True).returncode == 0


def vsechna_exe(drive_c):
    """Všechna .exe v prefixu (bez instalátorů, pomocníků a systémových souborů Wine)."""
    nalezene = set()
    for koren, _, soubory in os.walk(drive_c):
        for s in soubory:
            if not s.lower().endswith(".exe") or NE_HRY.search(s):
                continue
            cesta = os.path.join(koren, s)
            if "/windows/" in cesta.lower() or "/internet explorer/" in cesta.lower():
                continue
            nalezene.add(cesta)
    return nalezene


def nove_exe(drive_c, pred_instalaci):
    """Hry nainstalované od doby, kdy jsme startovali; největší nahoře.

    Čas úpravy nepoužíváme: instalátory často zachovají časy ze svého archivu,
    takže nově rozbalené soubory vypadají jako staré.
    """
    nove = vsechna_exe(drive_c) - pred_instalaci
    return sorted(((c, os.path.getsize(c)) for c in nove), key=lambda x: x[1], reverse=True)


def zapis_do_heroicu(nazev, prefix, exe):
    # Heroic musí být zavřený, jinak by si knihovnu přepsal zpátky
    app_name = "%016x" % random.getrandbits(64)
    slozka = os.path.dirname(exe)

    zaloha = time.strftime('%Y%m%d-%H%M%S')
    if os.path.exists(LIBRARY):
        shutil.copy2(LIBRARY, LIBRARY + f".pred-pridanim-{zaloha}")
        with open(LIBRARY, encoding="utf-8") as f:
            knihovna = json.load(f)
    else:
        knihovna = {"games": []}

    # Starý záznam se stejným názvem nahradíme: knihovnu očistíme a jeho nastavení odložíme do zálohy
    stare = [g for g in knihovna["games"] if g.get("title") == nazev]
    knihovna["games"] = [g for g in knihovna["games"] if g.get("title") != nazev]
    for g in stare:
        cfg = os.path.join(GAMES_CONFIG, f"{g['app_name']}.json")
        if os.path.exists(cfg):
            shutil.move(cfg, cfg + f".pred-prepsanim-{zaloha}")

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

    instalator = cesta_z_argumentu(sys.argv[1])
    if not os.path.isfile(instalator):
        sys.exit(f"Soubor {instalator} neexistuje (dostal jsem: {sys.argv[1]!r}).")
    if not os.path.isfile(UMU) or not os.path.isdir(PROTON):
        sys.exit("Nenašel jsem Heroic nástroje (umu, GE-Proton). Spusť Heroic a nainstaluj GE-Proton.")
    if heroic_bezi():
        sys.exit("Heroic běží. Zavři ho a spusť skript znovu.")

    nazev = sys.argv[2] if len(sys.argv) > 2 else os.path.basename(os.path.dirname(instalator))
    nazev = ask("Název hry", nazev)
    slozka_prefixu = re.sub(r"[^\w\- ]", "", nazev).strip() or "hra"
    prefix = os.path.join(PREFIXY, slozka_prefixu)
    if os.path.exists(prefix):
        print(f"Prefix {prefix} už existuje, přepisuju ho (starý se smaže).")
        shutil.rmtree(prefix)

    print(f"\nInstalátor: {instalator}")
    print(f"Prefix: {prefix}")
    os.makedirs(prefix)
    env = dict(os.environ, WINEPREFIX=prefix, PROTONPATH=PROTON, GAMEID="umu-default",
               STEAM_COMPAT_DATA_PATH=prefix)

    pred_instalaci = vsechna_exe(os.path.join(prefix, "drive_c"))
    proces = subprocess.Popen([sys.executable, UMU, instalator], env=env,
                              cwd=os.path.dirname(instalator))
    print("\nProjdi instalaci ve Wine okně. Cestu nech výchozí (C:\\...).")
    input("Až instalace skončí a okno instalátoru zavřeš, stiskni Enter... ")
    if proces.poll() is None:
        print("Instalátor ještě běží, počkám na něj.")
        proces.wait()

    drive_c = os.path.join(prefix, "drive_c")
    nalezene = nove_exe(drive_c, pred_instalaci)
    if not nalezene:
        sys.exit("Žádné nově nainstalované .exe jsem nenašel. Prefix zůstal, můžeš to zkusit znovu.")

    # Hra bývá nejen největší .exe, ale i největší soubor v její složce, proto bereme velikost
    print("\nNově nainstalované .exe (největší nahoře, nejspíš hra je 1):")
    for i, (cesta, velikost) in enumerate(nalezene, 1):
        print(f"  {i}. {os.path.relpath(cesta, drive_c)}  ({velikost // 1048576} MB)")
    volba = ask("Které .exe spouští hru (číslo)", "1")
    try:
        exe = nalezene[int(volba) - 1][0]
    except (ValueError, IndexError):
        sys.exit("Neplatná volba.")

    if heroic_bezi():
        sys.exit("Heroic je teď spuštěný, zavři ho a spusť skript znovu, ať se hra zapíše.")
    app_name = zapis_do_heroicu(nazev, prefix, exe)
    print(f"\nHotovo. Hra „{nazev}“ je v Heroicu (ID {app_name}).")
    print("Heroic spusť znovu a najdeš ji v knihovně.")


if __name__ == "__main__":
    main()
