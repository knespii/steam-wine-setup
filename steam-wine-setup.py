#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
steam-wine-setup.py
================
Z Windows hry (složka s .exe nebo instalátor setup.exe) udělá hru spouštěnou
přes Bottles nebo Heroic a přidá ji do Steamu jako ne-Steam hru.

Použití:
    python3 steam-wine-setup.py                      # vybereš Bottles/Heroic a hru ze seznamu
    python3 steam-wine-setup.py "MojeHra"            # název složky (hledá v SLOZKY_S_HRAMI)
    python3 steam-wine-setup.py ~/Hry/MojeHra        # celá cesta (i přímo k .exe nebo setup.exe)
    python3 steam-wine-setup.py --heroic "MojeHra"   # rovnou přes Heroic
    python3 steam-wine-setup.py --bottles "MojeHra"  # rovnou přes Bottles
    python3 steam-wine-setup.py --wine "/cesta/k/setup.exe"  # instalovat přes Wine, bez innoextractu

Co udělá:
  1. najde .exe ve složce a nechá tě vybrat to, které spouští hru (nebo instalátor)
  2. zeptá se, jestli hru spouštět přes Bottles, nebo přes Heroic
  3. instalátor (setup.exe):
       - Inno Setup (např. GOG) zkusí rozbalit bez Wine přes innoextract do SLOZKA_PRO_INSTALACE
       - když to nejde, spustí instalátor ve Wine a potom sám najde nově nainstalované .exe
  4. Bottles: vytvoří bottle (gaming, Fsync, GameMode – viz NASTAVENI_BOTTLE) a přidá do ní hru
     Heroic:  přidá hru do knihovny Heroicu, každá hra má vlastní Wine prefix
  5. zavře Steam, zazálohuje shortcuts.vdf a přidá hru do knihovny

Potřebuje: Python 3 (bez dalších balíčků), Bottles nebo Heroic (Flatpak i nativní), Steam.
Volitelně: innoextract (rozbalení instalátorů bez Wine), icoutils (ikona hry ve Steamu).
"""

import glob
import json
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unicodedata
import zlib
from datetime import datetime
from subprocess import DEVNULL, PIPE

# Složky s hrami: z jejich podsložek skript nabídne seznam her (a hledá v nich, když
# zadáš jen název hry). Uprav si podle sebe.
SLOZKY_S_HRAMI = ["/media/vikisekkk/viki-vault"] #["~/Documents/games", "~/Hry", "~/Games"]

# Podsložky, které v seznamu her nechceme (patří spouštěčům, ne hrám)
NE_HRY = {"heroic", "umu"}

# Kam rozbalovat hry z instalátorů přes innoextract (vznikne podsložka s názvem hry)
SLOZKA_PRO_INSTALACE = "~/Games"

# Přepínač --wine: instalovat vždy přes Wine, innoextract přeskočit (nastaví main)
PRESKOCIT_INNOEXTRACT = False

# Co skript zapne v každé NOVÉ bottle (existující bottle nemění). Uprav si podle sebe.
#   sync:     "fsync" (rychlé, jádro 5.16+), "esync", "ntsync" (jádro 6.14+), "wine" (výchozí Bottles)
#   gamemode: True = zapnout Feral GameMode (jen když je nainstalovaný: sudo apt install gamemode)
NASTAVENI_BOTTLE = {"sync": "fsync", "gamemode": True}

BOTTLES_FLATPAK = "com.usebottles.bottles"
HEROIC_FLATPAK = "com.heroicgameslauncher.hgl"
STEAM_FLATPAK = "com.valvesoftware.Steam"

# Kde může být Steam (druhá hodnota = jde o Flatpak verzi Steamu)
STEAM_KORENY = [
    ("~/.local/share/Steam", False),
    ("~/.steam/steam", False),
    ("~/.steam/root", False),
    ("~/.steam/debian-installation", False),
    ("~/.var/app/com.valvesoftware.Steam/.local/share/Steam", True),
    ("~/.var/app/com.valvesoftware.Steam/data/Steam", True),
]

# Typické podsložky, ve kterých hry mívají .exe (složka hry je nad nimi)
PODSLOZKY_EXE = {"bin", "bin32", "bin64", "x86", "x64", "win32", "win64", "binaries", "retail", "shipping"}

# .exe, které skoro jistě nespouští hru (instalátory, knihovny, crash reportery...)
NEHRA = re.compile(
    r"unins|uninst|setup|install|redist|vcredist|vc_redist|dxsetup|directx|"
    r"dotnet|ndp\d|crash|report|prereq|easyanticheat|battleye|cefprocess|"
    r"webhelper|qtwebengine|notification|update|patch",
    re.IGNORECASE,
)

# Název .exe, který vypadá jako instalátor hry
INSTALATOR = re.compile(r"setup|install", re.IGNORECASE)

# Složky Wine prefixu, ve kterých hra nebude (při hledání nově nainstalovaného .exe)
WINE_SYSTEM = re.compile(
    r"^(windows|programdata/microsoft|users/[^/]+/(temp|appdata/local/temp)|"
    r"program files[^/]*/(common files|internet explorer|windows [^/]+|windows nt|wine[^/]*|microsoft[^/]*|steam))/",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------
# výpis a otázky
# --------------------------------------------------------------------------

BARVY = sys.stdout.isatty()


def _c(kod, text):
    return f"\033[{kod}m{text}\033[0m" if BARVY else text


def krok(zprava):
    print("\n" + _c("1;34", "==> ") + _c("1", zprava))


def ok(zprava):
    print(_c("32", "  ✓ ") + zprava)


def pozor(zprava):
    print(_c("33", "  ! ") + zprava)


def konec(zprava):
    sys.stdout.flush()
    print(_c("31", "  ✗ ") + zprava, file=sys.stderr)
    sys.exit(1)


def zeptej(otazka, vychozi=""):
    navrh = f" [{vychozi}]" if vychozi else ""
    try:
        odpoved = input(f"  {otazka}{navrh}: ").strip()
    except EOFError:
        print()
        odpoved = ""
    return odpoved or vychozi


def ano(otazka, vychozi=True):
    odpoved = zeptej(f"{otazka} ({'A/n' if vychozi else 'a/N'})").lower()
    if not odpoved:
        return vychozi
    return odpoved[0] in ("a", "y")


def vyber(otazka, polozky, vychozi=1):
    """Vypíše očíslovaný seznam a vrátí index vybrané položky."""
    for i, polozka in enumerate(polozky, 1):
        print(f"    {i}) {polozka}")
    while True:
        odpoved = zeptej(otazka, str(vychozi))
        if odpoved.isdigit() and 1 <= int(odpoved) <= len(polozky):
            return int(odpoved) - 1
        pozor("Zadej číslo ze seznamu.")


# --------------------------------------------------------------------------
# hra: složka, .exe, názvy
# --------------------------------------------------------------------------

def najdi_slozku(zadani):
    """Vrátí (složka, exe nebo None). Přijme název, cestu ke složce i cestu k .exe."""
    zadani = zadani.strip().strip("'\"")
    if not zadani:
        return None, None
    kandidati = [os.path.expanduser(zadani)]
    if "/" not in zadani and not zadani.startswith("~"):
        kandidati += [os.path.join(os.path.expanduser(s), zadani) for s in SLOZKY_S_HRAMI]
    for k in kandidati:
        if os.path.isfile(k) and k.lower().endswith(".exe"):
            exe = os.path.realpath(k)
            slozka = os.path.dirname(exe)
            # .exe v bin/, Binaries/Win64/ apod. → složka hry je o úroveň (nebo víc) výš
            while os.path.basename(slozka).lower() in PODSLOZKY_EXE and os.path.dirname(slozka) != slozka:
                slozka = os.path.dirname(slozka)
            return slozka, exe
        if os.path.isdir(k):
            return os.path.realpath(k), None
    return None, None


def ma_exe(slozka, max_hloubka=4):
    """Je ve složce (do hloubky max_hloubka) aspoň jedno .exe? Skončí u prvního nalezeného."""
    for koren, podslozky, soubory in os.walk(slozka):
        if any(s.lower().endswith(".exe") for s in soubory):
            return True
        if os.path.relpath(koren, slozka).count(os.sep) >= max_hloubka - 1:
            podslozky[:] = []
    return False


def zkrat(cesta):
    domov = os.path.expanduser("~")
    return "~" + cesta[len(domov):] if cesta == domov or cesta.startswith(domov + "/") else cesta


def vyber_hru():
    """Nabídne hry (podsložky s .exe) ze SLOZKY_S_HRAMI. Vrátí cestu, nebo to, co napíšeš."""
    hry = []
    for zaklad in SLOZKY_S_HRAMI:
        zaklad = os.path.expanduser(zaklad)
        if not os.path.isdir(zaklad):
            continue
        for jmeno in sorted(os.listdir(zaklad), key=str.lower):
            cesta = os.path.join(zaklad, jmeno)
            if (os.path.isdir(cesta) and not jmeno.startswith(".")
                    and jmeno.lower() not in NE_HRY and ma_exe(cesta)):
                hry.append(cesta)
    if not hry:
        return zeptej("Název nebo cesta ke složce s .exe")
    for i, cesta in enumerate(hry, 1):
        print(f"    {i}) {os.path.basename(cesta)}   ({zkrat(os.path.dirname(cesta))})")
    print("    c) zadat jinou cestu")
    while True:
        odpoved = zeptej("Kterou hru?").lower()
        if odpoved.isdigit() and 1 <= int(odpoved) <= len(hry):
            return hry[int(odpoved) - 1]
        if odpoved == "c":
            return zeptej("Cesta ke složce s .exe (nebo k .exe)")
        pozor("Zadej číslo ze seznamu, nebo c.")


def najdi_exe(slozka, max_hloubka=6):
    nalezene = []
    for koren, podslozky, soubory in os.walk(slozka):
        if os.path.relpath(koren, slozka).count(os.sep) >= max_hloubka:
            podslozky[:] = []
        for soubor in soubory:
            if soubor.lower().endswith(".exe"):
                cesta = os.path.join(koren, soubor)
                try:
                    nalezene.append((cesta, os.path.getsize(cesta)))
                except OSError:
                    pass
    return nalezene


def najdi_nova_exe(drive_c, od_casu):
    """.exe ve Wine prefixu, která vznikla po čase od_casu (podle ctime), mimo systémové složky."""
    nalezene = []
    for koren, podslozky, soubory in os.walk(drive_c):
        rel = os.path.relpath(koren, drive_c).replace(os.sep, "/")
        if rel != "." and WINE_SYSTEM.match(rel + "/"):
            podslozky[:] = []
            continue
        for soubor in soubory:
            if soubor.lower().endswith(".exe"):
                cesta = os.path.join(koren, soubor)
                try:
                    st = os.stat(cesta)
                except OSError:
                    continue
                if st.st_ctime >= od_casu:
                    nalezene.append((cesta, st.st_size))
    return nalezene


def vyber_exe(slozka, nalezene=None, otazka="Které .exe spouští hru?"):
    vse = najdi_exe(slozka) if nalezene is None else nalezene
    if not vse:
        konec(f"Ve složce {slozka} jsem nenašel žádné .exe.")

    def je_hra(cesta):
        return not NEHRA.search(os.path.relpath(cesta, slozka))

    hry = sorted((x for x in vse if je_hra(x[0])), key=lambda x: -x[1])
    ostatni = sorted((x for x in vse if not je_hra(x[0])), key=lambda x: -x[1])
    seznam = hry or ostatni
    vse_zobrazeno = not hry

    while True:
        for i, (cesta, velikost) in enumerate(seznam, 1):
            print(f"    {i}) {os.path.relpath(cesta, slozka)}  ({velikost / 1048576:.1f} MB)")
        if ostatni and not vse_zobrazeno:
            print(f"    v) ukázat i ostatní .exe ({len(ostatni)}× instalátor, redist apod.)")
        odpoved = zeptej(otazka, "1").lower()
        if odpoved == "v" and not vse_zobrazeno:
            seznam, vse_zobrazeno = hry + ostatni, True
            continue
        if odpoved.isdigit() and 1 <= int(odpoved) <= len(seznam):
            return seznam[int(odpoved) - 1][0]
        pozor("Zadej číslo ze seznamu.")


def ma_inno_podpis(exe, max_bajtu=64 * 1048576):
    """Je v .exe podpis Inno Setup (instalátor)? Čte jen začátek souboru."""
    try:
        with open(exe, "rb") as f:
            zbytek = b""
            precteno = 0
            while precteno < max_bajtu:
                blok = f.read(1048576)
                if not blok:
                    break
                precteno += len(blok)
                if b"Inno Setup" in zbytek + blok:
                    return True
                zbytek = blok[-16:]
    except OSError:
        return False
    return False


def je_instalator(exe):
    jmeno = os.path.basename(exe)
    if jmeno.lower().startswith("unins"):
        return False
    return bool(INSTALATOR.search(jmeno)) or ma_inno_podpis(exe)


def nazev_programu(text):
    """Název hry bez znaků, které by rozbily příkaz ve Steamu (uvozovky, $, ` ...)."""
    text = re.sub(r"[^\w \-.,()+]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def nazev_bottle(text):
    """Název bottle jen z ASCII písmen, čísel, - a _ (Bottles z něj dělá i složku)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-")
    return text or "Hra"


def volna_slozka(zaklad):
    """zaklad, nebo zaklad-2, zaklad-3... – první, která ještě neexistuje."""
    if not os.path.exists(zaklad):
        return zaklad
    i = 2
    while os.path.exists(f"{zaklad}-{i}"):
        i += 1
    return f"{zaklad}-{i}"


# --------------------------------------------------------------------------
# Flatpak
# --------------------------------------------------------------------------

def flatpak_ma(app_id):
    if not shutil.which("flatpak"):
        return False
    return subprocess.run(["flatpak", "info", app_id], stdout=DEVNULL, stderr=DEVNULL).returncode == 0


def flatpak_vidi_slozku(app_id, slozka):
    """Má Flatpak aplikace zápis do složky? (podle 'flatpak info --show-permissions')"""
    try:
        out = subprocess.run(
            ["flatpak", "info", "--show-permissions", app_id],
            stdout=PIPE, stderr=DEVNULL, text=True,
        ).stdout
    except OSError:
        return False
    m = re.search(r"^filesystems=(.*)$", out, re.M)
    if not m:
        return False
    domov = os.path.expanduser("~")
    for polozka in m.group(1).split(";"):
        cesta, _, rezim = polozka.strip().partition(":")
        if not cesta or cesta.startswith("!") or rezim == "ro":
            continue
        if cesta == "host":
            return True
        if cesta == "home":
            cesta = domov
        elif cesta.startswith("~"):
            cesta = os.path.expanduser(cesta)
        elif not cesta.startswith("/"):
            continue  # xdg-download apod. neřešíme
        cesta = cesta.rstrip("/") or "/"
        if slozka == cesta or slozka.startswith(cesta.rstrip("/") + "/"):
            return True
    return False


def zajisti_pristup(app_id, nazev, slozka):
    """Zeptá se a povolí Flatpak aplikaci přístup jen k téhle jedné složce."""
    if flatpak_vidi_slozku(app_id, slozka):
        return True
    pozor(f"Flatpak {nazev} je v sandboxu a do {slozka} zatím nevidí.")
    if ano(f"Povolit {nazev} přístup k {slozka}?"):
        r = subprocess.run(["flatpak", "override", "--user", f"--filesystem={slozka}", app_id])
        if r.returncode == 0:
            ok("Přístup povolen")
            return True
        pozor("flatpak override selhal.")
        return False
    pozor(f"Bez přístupu {nazev} hru nespustí.")
    return False


# --------------------------------------------------------------------------
# instalátory
# --------------------------------------------------------------------------

def rozbal_innoextract(instalator, program):
    """Rozbalí instalátor Inno Setup bez Wine. Vrátí složku s hrou, nebo None."""
    if PRESKOCIT_INNOEXTRACT:
        return None
    if not shutil.which("innoextract"):
        pozor("innoextract není nainstalovaný (sudo apt install innoextract), instaluju přes Wine.")
        return None
    if subprocess.run(["innoextract", "-i", instalator], stdout=DEVNULL, stderr=DEVNULL).returncode != 0:
        pozor("Tenhle instalátor innoextract neumí rozbalit, instaluju přes Wine.")
        return None
    cil = volna_slozka(os.path.join(os.path.expanduser(SLOZKA_PRO_INSTALACE), program))
    krok(f"Rozbaluju hru bez Wine do {cil}")
    os.makedirs(cil)
    # -g: GOG instalátory mají data v přídavných archivech (.bin), bez -g by hra chyběla
    r = subprocess.run(["innoextract", "-g", "-m", "-q", "-p", "-d", cil, instalator])
    hra = os.path.join(cil, "app") if os.path.isdir(os.path.join(cil, "app")) else cil
    exe_hry = [x for x in najdi_exe(hra) if not NEHRA.search(os.path.relpath(x[0], hra))]
    if r.returncode == 0 and exe_hry:
        ok("Rozbaleno")
        return hra
    pozor("V rozbalených souborech žádná hra není (tenhle instalátor si data rozbaluje sám), "
          "instaluju přes Wine.")
    shutil.rmtree(cil, ignore_errors=True)
    return None


def nainstaluj(instalator, program, spust_ve_wine, najdi_drive_c, pristup):
    """Nainstaluje hru z instalátoru. Vrátí (exe hry, složka hry).

    spust_ve_wine()  – spustí instalátor ve Wine prefixu hry
    najdi_drive_c()  – vrátí cestu k drive_c toho prefixu
    pristup(slozka)  – zajistí, že Flatpak spouštěč do složky vidí
    """
    hra = rozbal_innoextract(instalator, program)
    if hra:
        pristup(hra)
        return vyber_exe(hra), hra

    pristup(os.path.dirname(instalator))
    krok("Spouštím instalátor ve Wine")
    print("  Projdi instalaci. Cestu nech výchozí (C:\\...), hra se nainstaluje dovnitř prefixu.")
    zacatek = time.time() - 2
    spust_ve_wine()
    zeptej("Až instalace skončí a okno instalátoru zavřeš, stiskni Enter")

    drive_c = najdi_drive_c()
    nova = najdi_nova_exe(drive_c, zacatek) if drive_c and os.path.isdir(drive_c) else []
    if nova:
        krok("Nově nainstalované .exe")
        hry_nove = [x for x in nova if not NEHRA.search(os.path.basename(x[0]))]
        if len(hry_nove) == 1:
            exe = hry_nove[0][0]
            ok(f"Hra nalezena automaticky: {os.path.relpath(exe, drive_c)}")
        else:
            exe = vyber_exe(drive_c, nova)
        return exe, os.path.dirname(exe)

    pozor("Žádné nově nainstalované .exe jsem nenašel.")
    cesta = zeptej("Zadej cestu k .exe hry ručně (Enter = konec)")
    cesta = os.path.expanduser(cesta.strip().strip("'\""))
    if not cesta:
        konec("Hra se nenainstalovala, končím. Bottle/prefix zůstal, můžeš to zkusit znovu.")
    if not os.path.isfile(cesta):
        konec(f"Soubor {cesta} neexistuje.")
    return os.path.realpath(cesta), os.path.dirname(os.path.realpath(cesta))


# --------------------------------------------------------------------------
# Bottles
# --------------------------------------------------------------------------

class Bottles:
    def __init__(self, druh):
        self.druh = druh  # "flatpak" nebo "nativni"
        if druh == "flatpak":
            self.prikaz = ["flatpak", "run", "--command=bottles-cli", BOTTLES_FLATPAK]
        else:
            self.prikaz = [shutil.which("bottles-cli") or "bottles-cli"]

    def spust(self, *argumenty, zachytit=True):
        cmd = self.prikaz + list(argumenty)
        sys.stdout.flush()
        if not zachytit:
            return subprocess.run(cmd).returncode, "", ""
        r = subprocess.run(cmd, stdout=PIPE, stderr=PIPE, text=True)
        return r.returncode, r.stdout, r.stderr

    def jmena(self, *argumenty, klic=None):
        """Seznam jmen z 'bottles-cli list ...' – zkusí JSON, jinak textový výstup."""
        _, out, _ = self.spust("-j", *argumenty)
        data = _json_z_vystupu(out)
        if isinstance(data, dict) and klic and klic in data:
            data = data[klic]
        if isinstance(data, dict):
            return list(data.keys())
        if isinstance(data, list):
            return [x.get("name", "") if isinstance(x, dict) else str(x) for x in data]
        _, out, _ = self.spust(*argumenty)
        return [m.group(1).strip() for m in re.finditer(r"^\s*- (.+)$", out, re.M)]

    def programy(self, bottle):
        """Programy v bottle jako [(jméno, cesta nebo None)]."""
        _, out, _ = self.spust("-j", "programs", "-b", bottle)
        data = _json_z_vystupu(out)
        if isinstance(data, list):
            return [(p.get("name", ""), p.get("path")) for p in data if isinstance(p, dict)]
        return [(j, None) for j in self.jmena("programs", "-b", bottle)]

    def drive_c(self, bottle):
        _, out, _ = self.spust("info", "bottles-path")
        cesty = [r.strip() for r in out.splitlines() if r.strip().startswith("/")]
        if cesty:
            zaklad = cesty[-1]
        elif self.druh == "flatpak":
            zaklad = os.path.expanduser(f"~/.var/app/{BOTTLES_FLATPAK}/data/bottles/bottles")
        else:
            zaklad = os.path.expanduser("~/.local/share/bottles/bottles")
        return os.path.join(zaklad, bottle, "drive_c")


def _json_z_vystupu(text):
    for radek in [text.strip()] + text.splitlines()[::-1]:
        radek = radek.strip()
        if radek.startswith(("{", "[")):
            try:
                return json.loads(radek)
            except ValueError:
                continue
    return None


def vyber_bottles():
    nativni = bool(shutil.which("bottles-cli"))
    flatpak = flatpak_ma(BOTTLES_FLATPAK)
    if nativni and flatpak:
        i = vyber("Máš Bottles dvakrát. Kterou verzi použít?", ["Flatpak", "nativní (bottles-cli)"])
        return Bottles("flatpak" if i == 0 else "nativni")
    if flatpak:
        return Bottles("flatpak")
    if nativni:
        return Bottles("nativni")
    konec(
        "Nenašel jsem Bottles. Nainstaluj je (doporučeně přes Flatpak):\n"
        f"      flatpak install flathub {BOTTLES_FLATPAK}\n"
        "    a jednou je spusť, ať doběhne první nastavení."
    )


def nastav_bottle(bottles, bottle):
    """Zapne v nové bottle volby z NASTAVENI_BOTTLE (přes 'bottles-cli edit --params')."""
    popisky = {"sync": "Synchronizace", "gamemode": "GameMode"}
    for klic, hodnota in NASTAVENI_BOTTLE.items():
        if klic == "gamemode" and hodnota and not shutil.which("gamemoded"):
            pozor("GameMode není nainstalovaný (sudo apt install gamemode), nechávám ho vypnutý.")
            continue
        text = str(hodnota).lower() if isinstance(hodnota, bool) else str(hodnota)
        kod, out, err = bottles.spust("edit", "-b", bottle, "--params", f"{klic}:{text}")
        if kod == 0:
            citelne = {"true": "zapnuto", "false": "vypnuto"}.get(text, text)
            ok(f"{popisky.get(klic, klic)}: {citelne}")
        else:
            chyba = (err or out).strip().splitlines()
            pozor(f"Nepovedlo se nastavit {klic}:{text} ({chyba[-1] if chyba else 'neznámá chyba'}). "
                  "Zapni to ručně v Bottles → Settings.")


def pres_bottles(program, slozka, exe, instalator):
    """Bottle + program v ní. Vrátí (exe hry, složka hry, příkaz pro Steam, příkaz pro terminál)."""
    krok("Bottles")
    bottles = vyber_bottles()
    ok(f"Bottles: {'Flatpak' if bottles.druh == 'flatpak' else 'nativní'}")

    def pristup(cesta):
        if bottles.druh == "flatpak":
            zajisti_pristup(BOTTLES_FLATPAK, "Bottles", cesta)

    bottle = nazev_bottle(program)
    existujici = bottles.jmena("list", "bottles")
    pouzit_existujici = False
    while bottle in existujici:
        if ano(f"Bottle „{bottle}“ už existuje. Použít ji?"):
            pouzit_existujici = True
            break
        bottle = nazev_bottle(zeptej("Zadej jiný název bottle", bottle + "-2"))

    if not pouzit_existujici:
        runnery = bottles.jmena("list", "components", "-f", "category:runners", klic="runners")
        runner = None
        if runnery:
            moznosti = ["výchozí runner Bottles (doporučeno)"] + [
                r + ("   ← Proton" if "proton" in r.lower() else "") for r in runnery
            ]
            i = vyber("Runner (Proton runnery se instalují v Bottles → Předvolby → Runnery)", moznosti)
            runner = runnery[i - 1] if i > 0 else None
        krok(f"Vytvářím bottle „{bottle}“ (poprvé to může trvat i pár minut)")
        argumenty = ["new", "--bottle-name", bottle, "--environment", "gaming"]
        if runner:
            argumenty += ["--runner", runner]
        kod, _, _ = bottles.spust(*argumenty, zachytit=False)
        if kod != 0:
            konec("Vytvoření bottle selhalo (chyba je vypsaná výš).")
        if bottle not in bottles.jmena("list", "bottles"):
            pozor("Bottles hlásí úspěch, ale bottle v seznamu nevidím. Zkontroluj to v Bottles.")
        ok(f"Bottle „{bottle}“ je hotová{' s runnerem ' + runner if runner else ''}")
        nastav_bottle(bottles, bottle)

    if instalator:
        exe, slozka = nainstaluj(
            instalator, program,
            spust_ve_wine=lambda: bottles.spust("run", "-b", bottle, "-e", instalator, zachytit=False),
            najdi_drive_c=lambda: bottles.drive_c(bottle),
            pristup=pristup,
        )
    else:
        pristup(slozka)

    krok("Přidávám hru do bottle")
    while True:
        stejne = [cesta for jmeno, cesta in bottles.programy(bottle) if jmeno == program]
        if not stejne or exe in stejne or all(c is None for c in stejne):
            break
        pozor(f"V bottle už je program „{program}“, ale vede jinam ({stejne[0]}).")
        if zeptej("V Bottles u něj dej ⋮ → Remove from list a stiskni Enter (k = konec)").lower() == "k":
            konec("Nic jsem nepřidal.")

    if stejne and (exe in stejne or all(c is None for c in stejne)):
        ok(f"Program „{program}“ už v bottle je")
    else:
        kod, out, err = bottles.spust("add", "-b", bottle, "-n", program, "-p", exe)
        if kod != 0 or "added" not in out:
            konec(f"Přidání programu selhalo:\n{(out + err).strip()}")
        ok(f"„{program}“ přidán do „{bottle}“")

    beh = f'run -b "{bottle}" -p "{program}"'
    if bottles.druh == "flatpak":
        pro_steam = (shutil.which("flatpak") or "/usr/bin/flatpak",
                     f"run --command=bottles-cli {BOTTLES_FLATPAK} {beh}")
    else:
        pro_steam = (bottles.prikaz[0], beh)
    terminal = " ".join(bottles.prikaz) + " " + beh
    return exe, slozka, pro_steam, terminal


# --------------------------------------------------------------------------
# Heroic
# --------------------------------------------------------------------------

class Heroic:
    def __init__(self, druh):
        self.druh = druh  # "flatpak" nebo "nativni"
        if druh == "flatpak":
            self.prikaz = ["flatpak", "run", HEROIC_FLATPAK]
            self.config = os.path.expanduser(f"~/.var/app/{HEROIC_FLATPAK}/config/heroic")
        else:
            self.prikaz = [shutil.which("heroic") or "heroic"]
            self.config = os.path.expanduser("~/.config/heroic")
        self.knihovna = os.path.join(self.config, "sideload_apps", "library.json")
        self.nastaveni_her = os.path.join(self.config, "GamesConfig")
        self._zalohovano = False

    def globalni(self):
        try:
            with open(os.path.join(self.config, "config.json"), encoding="utf-8") as f:
                return json.load(f).get("defaultSettings", {})
        except (OSError, ValueError):
            return {}

    def bezi(self):
        if self.druh == "flatpak":
            out = subprocess.run(["flatpak", "ps", "--columns=application"],
                                 stdout=PIPE, stderr=DEVNULL, text=True).stdout
            return HEROIC_FLATPAK in out
        return subprocess.run(["pgrep", "-x", "heroic"], stdout=DEVNULL, stderr=DEVNULL).returncode == 0

    def zavri(self):
        if not self.bezi():
            return True
        pozor("Heroic běží – musí být zavřený, než do jeho knihovny zapíšu hru.")
        if ano("Mám Heroic zavřít?"):
            if self.druh == "flatpak":
                subprocess.run(["flatpak", "kill", HEROIC_FLATPAK], stdout=DEVNULL, stderr=DEVNULL)
            else:
                subprocess.run(["pkill", "-x", "heroic"], stdout=DEVNULL, stderr=DEVNULL)
        else:
            zeptej("Zavři Heroic ručně (i z lišty) a stiskni Enter")
        for _ in range(20):
            if not self.bezi():
                time.sleep(1)
                return True
            time.sleep(1)
        return False

    def _nacti(self):
        if not os.path.exists(self.knihovna):
            return {"games": []}
        try:
            with open(self.knihovna, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            konec(f"Nerozumím souboru {self.knihovna} ({e}). Nic jsem neměnil.")

    def najdi_hru(self, title):
        for hra in self._nacti().get("games", []):
            if hra.get("runner") == "sideload" and str(hra.get("title", "")).lower() == title.lower():
                return hra.get("app_name")
        return None

    def uloz_hru(self, app_name, title, exe):
        data = self._nacti()
        if os.path.exists(self.knihovna) and not self._zalohovano:
            zaloha = f"{self.knihovna}.zaloha-{datetime.now():%Y%m%d-%H%M%S}"
            shutil.copy2(self.knihovna, zaloha)
            ok(f"Záloha knihovny Heroicu: {zaloha}")
            self._zalohovano = True
        hry = data.setdefault("games", [])
        zaznam = {
            "runner": "sideload",
            "app_name": app_name,
            "title": title,
            "install": {"executable": exe, "platform": "Windows", "is_dlc": False},
            "folder_name": os.path.dirname(exe),
            "art_cover": "",
            "art_square": "",
            "is_installed": True,
            "canRunOffline": True,
        }
        for i, hra in enumerate(hry):
            if hra.get("app_name") == app_name:
                hry[i] = {**hra, **zaznam, "art_cover": hra.get("art_cover", ""),
                          "art_square": hra.get("art_square", "")}
                break
        else:
            hry.append(zaznam)
        os.makedirs(os.path.dirname(self.knihovna), exist_ok=True)
        docasny = self.knihovna + ".novy"
        with open(docasny, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent="\t")
        os.replace(docasny, self.knihovna)

    def nastaveni(self, app_name):
        try:
            with open(os.path.join(self.nastaveni_her, f"{app_name}.json"), encoding="utf-8") as f:
                return json.load(f).get(app_name, {})
        except (OSError, ValueError):
            return None

    def uloz_nastaveni(self, app_name, prefix, wine):
        """Vlastní prefix pro hru. Existující nastavení hry nemění."""
        if self.nastaveni(app_name) is not None:
            return
        os.makedirs(self.nastaveni_her, exist_ok=True)
        with open(os.path.join(self.nastaveni_her, f"{app_name}.json"), "w", encoding="utf-8") as f:
            json.dump({app_name: {"winePrefix": prefix, "wineVersion": wine},
                       "version": "v0", "explicit": True}, f, ensure_ascii=False, indent="\t")

    def url(self, app_name):
        return f"heroic://launch?appName={app_name}&runner=sideload"

    def spust_hru(self, app_name):
        sys.stdout.flush()
        subprocess.Popen(self.prikaz + ["--no-gui", "--no-sandbox", self.url(app_name)],
                         stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)

    def pro_steam(self, app_name):
        argumenty = f'--no-gui --no-sandbox "{self.url(app_name)}"'
        if self.druh == "flatpak":
            return shutil.which("flatpak") or "/usr/bin/flatpak", f"run {HEROIC_FLATPAK} {argumenty}"
        return self.prikaz[0], argumenty


def najdi_heroic():
    if flatpak_ma(HEROIC_FLATPAK):
        return Heroic("flatpak")
    if shutil.which("heroic"):
        return Heroic("nativni")
    return None


def pres_heroic(program, slozka, exe, instalator):
    """Hra v knihovně Heroicu. Vrátí (exe hry, složka hry, příkaz pro Steam, příkaz pro terminál)."""
    krok("Heroic")
    heroic = najdi_heroic()
    ok(f"Heroic: {'Flatpak' if heroic.druh == 'flatpak' else 'nativní'}")
    globalni = heroic.globalni()
    if not globalni:
        konec("Heroic ještě nemá nastavení. Spusť ho jednou, zavři ho a spusť skript znovu.")
    wine = globalni.get("wineVersion") or {}
    if not wine.get("bin"):
        konec("V Heroicu není vybrané Wine/Proton. Otevři Heroic → Wine Manager, stáhni GE-Proton,\n"
              "    v Settings → Game Defaults ho vyber jako Wine Version a spusť skript znovu.")
    ok(f"Wine/Proton: {wine.get('name', wine.get('bin'))}")

    def pristup(cesta):
        if heroic.druh == "flatpak":
            zajisti_pristup(HEROIC_FLATPAK, "Heroic", cesta)

    app_name = heroic.najdi_hru(program)
    if app_name:
        if not ano(f"V Heroicu už je hra „{program}“. Přepsat ji?"):
            konec("Heroic nechávám beze změny.")
    else:
        app_name = secrets.token_hex(8)

    stare = heroic.nastaveni(app_name) or {}
    zaklad = os.path.expanduser(globalni.get("defaultWinePrefix") or "~/Games/Heroic/Prefixes/default")
    prefix = os.path.expanduser(stare.get("winePrefix") or os.path.join(zaklad, program))
    ok(f"Wine prefix hry: {prefix}")

    if instalator:
        def spust_ve_wine():
            if not heroic.zavri():
                konec("Heroic se nezavřel. Zavři ho a spusť skript znovu.")
            heroic.uloz_hru(app_name, program, instalator)
            heroic.uloz_nastaveni(app_name, prefix, wine)
            print("  Heroic spustí instalátor (první spuštění prefixu chvíli trvá).")
            heroic.spust_hru(app_name)

        def najdi_drive_c():
            for kandidat in (os.path.join(prefix, "pfx", "drive_c"), os.path.join(prefix, "drive_c")):
                if os.path.isdir(kandidat):
                    return kandidat
            return None

        exe, slozka = nainstaluj(instalator, program, spust_ve_wine, najdi_drive_c, pristup)
    else:
        pristup(slozka)

    krok("Přidávám hru do Heroicu")
    if not heroic.zavri():
        konec("Heroic se nezavřel. Zavři ho a spusť skript znovu.")
    heroic.uloz_hru(app_name, program, exe)
    heroic.uloz_nastaveni(app_name, prefix, wine)
    ok(f"„{program}“ je v knihovně Heroicu")

    terminal = " ".join(heroic.prikaz) + f' --no-gui "{heroic.url(app_name)}"'
    return exe, slozka, heroic.pro_steam(app_name), terminal


def vyber_spoustec(predvoleny):
    ma_bottles = bool(shutil.which("bottles-cli")) or flatpak_ma(BOTTLES_FLATPAK)
    ma_heroic = najdi_heroic() is not None
    if predvoleny == "heroic" and not ma_heroic:
        konec(f"Heroic jsem nenašel. Nainstaluj ho: flatpak install flathub {HEROIC_FLATPAK}")
    if predvoleny == "bottles" and not ma_bottles:
        konec(f"Bottles jsem nenašel. Nainstaluj je: flatpak install flathub {BOTTLES_FLATPAK}")
    if predvoleny:
        return predvoleny
    if ma_bottles and ma_heroic:
        i = vyber("Přes co hru spouštět?", [
            "Bottles  – bezpečnější, vidí jen složku hry; Fsync, GameMode, snadné doinstalování knihoven",
            "Heroic   – vlastní Wine prefix a GE-Proton z Heroicu; zkus, když hra v Bottles nejde",
        ])
        return "bottles" if i == 0 else "heroic"
    if ma_heroic:
        return "heroic"
    if ma_bottles:
        return "bottles"
    konec("Nenašel jsem Bottles ani Heroic. Nainstaluj jeden z nich, třeba:\n"
          f"      flatpak install flathub {BOTTLES_FLATPAK}")


# --------------------------------------------------------------------------
# Steam: binární shortcuts.vdf
# --------------------------------------------------------------------------

class Surova:
    """Hodnota typu, který neměníme (float, uint64) – zapíše se zpátky beze změny."""

    def __init__(self, typ, data):
        self.typ, self.data = typ, data


def vdf_nacti(data):
    pozice = 0

    def retezec():
        nonlocal pozice
        konec_ = data.index(b"\x00", pozice)
        s = data[pozice:konec_].decode("utf-8", "surrogateescape")
        pozice = konec_ + 1
        return s

    def mapa(koren=False):
        nonlocal pozice
        vysledek = {}
        while pozice < len(data):
            typ = data[pozice]
            pozice += 1
            if typ == 0x08:
                return vysledek
            klic = retezec()
            if typ == 0x00:
                vysledek[klic] = mapa()
            elif typ == 0x01:
                vysledek[klic] = retezec()
            elif typ == 0x02:
                vysledek[klic] = struct.unpack_from("<I", data, pozice)[0]
                pozice += 4
            elif typ == 0x03:
                vysledek[klic] = Surova(typ, data[pozice:pozice + 4])
                pozice += 4
            elif typ == 0x07:
                vysledek[klic] = Surova(typ, data[pozice:pozice + 8])
                pozice += 8
            else:
                raise ValueError(f"neznámý typ 0x{typ:02x} na pozici {pozice - 1}")
        if koren:
            return vysledek
        raise ValueError("soubor končí uprostřed záznamu")

    return mapa(koren=True) if data else {}


def vdf_zapis(mapa):
    out = bytearray()
    for klic, hodnota in mapa.items():
        k = klic.encode("utf-8", "surrogateescape") + b"\x00"
        if isinstance(hodnota, dict):
            out += b"\x00" + k + vdf_zapis(hodnota)
        elif isinstance(hodnota, str):
            out += b"\x01" + k + hodnota.encode("utf-8", "surrogateescape") + b"\x00"
        elif isinstance(hodnota, int):
            out += b"\x02" + k + struct.pack("<I", hodnota & 0xFFFFFFFF)
        elif isinstance(hodnota, Surova):
            out += bytes([hodnota.typ]) + k + hodnota.data
        else:
            raise TypeError(f"neumím zapsat {klic}={hodnota!r}")
    out += b"\x08"
    return bytes(out)


def _ziskej(mapa, klic, vychozi=None):
    """Hodnota podle klíče bez ohledu na velikost písmen (starší Steam píše 'appname')."""
    for k, v in mapa.items():
        if k.lower() == klic.lower():
            return v
    return vychozi


def najdi_steam():
    nalezene, videne = [], set()
    for cesta, je_flatpak in STEAM_KORENY:
        p = os.path.expanduser(cesta)
        if not os.path.isdir(os.path.join(p, "userdata")):
            continue
        skutecna = os.path.realpath(p)
        if skutecna in videne:
            continue
        videne.add(skutecna)
        nalezene.append((skutecna, je_flatpak or f"/.var/app/{STEAM_FLATPAK}/" in skutecna + "/"))
    return nalezene


def steam_uzivatele(koren):
    jmena, posledni = {}, None
    try:
        with open(os.path.join(koren, "config", "loginusers.vdf"), encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in re.finditer(r'"(\d{17})"\s*\{(.*?)\}', text, re.S):
            ucet = str(int(m.group(1)) - 76561197960265728)
            jmeno = re.search(r'"PersonaName"\s*"([^"]*)"', m.group(2), re.I)
            jmena[ucet] = jmeno.group(1) if jmeno else ""
            if re.search(r'"MostRecent"\s*"1"', m.group(2), re.I):
                posledni = ucet
    except OSError:
        pass
    ids = sorted(d for d in os.listdir(os.path.join(koren, "userdata")) if d.isdigit() and d != "0")
    return ids, jmena, posledni


def steam_bezi():
    try:
        return subprocess.run(["pgrep", "-x", "steam"], stdout=DEVNULL, stderr=DEVNULL).returncode == 0
    except FileNotFoundError:
        return None


def zavri_steam(flatpak_steam):
    stav = steam_bezi()
    if stav is False:
        return True
    if stav is None:
        zeptej("Nevím, jestli běží Steam. Pro jistotu ho zavři a stiskni Enter")
        return True
    pozor("Steam běží – při zavření by si zástupce přepsal zpátky, musí být vypnutý.")
    if ano("Mám Steam zavřít?"):
        if flatpak_steam:
            cmd = ["flatpak", "run", STEAM_FLATPAK, "-shutdown"]
        else:
            cmd = [shutil.which("steam") or "steam", "-shutdown"]
        try:
            subprocess.Popen(cmd, stdout=DEVNULL, stderr=DEVNULL, start_new_session=True)
        except OSError:
            zeptej("Nepovedlo se. Zavři Steam ručně a stiskni Enter")
    else:
        zeptej("Zavři Steam ručně a stiskni Enter")
    print("  Čekám, až se Steam vypne...", end="", flush=True)
    for _ in range(60):
        if not steam_bezi():
            print()
            time.sleep(2)  # ať Steam stihne dopsat své soubory
            return True
        time.sleep(1)
        print(".", end="", flush=True)
    print()
    return False


def vytahni_ikonu(exe, cil_png):
    """Vytáhne největší ikonu z .exe (potřebuje icoutils). Vrátí True/False."""
    if not (shutil.which("wrestool") and shutil.which("icotool")):
        return False
    try:
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["wrestool", "-x", "-t", "14", "-o", tmp, exe], stdout=DEVNULL, stderr=DEVNULL)
            for ico in glob.glob(os.path.join(tmp, "*.ico")):
                subprocess.run(["icotool", "-x", "-o", tmp, ico], stdout=DEVNULL, stderr=DEVNULL)
            pngs = glob.glob(os.path.join(tmp, "*.png"))
            if not pngs:
                return False

            def velikost(p):
                m = re.search(r"_(\d+)x\d+x(\d+)\.png$", p)
                return (int(m.group(1)), int(m.group(2))) if m else (0, os.path.getsize(p))

            os.makedirs(os.path.dirname(cil_png), exist_ok=True)
            shutil.copyfile(max(pngs, key=velikost), cil_png)
            return True
    except Exception:
        return False


def pridej_do_steamu(program, exe_hry, slozka_hry, pro_steam, stitek):
    """pro_steam = (program, argumenty), které má Steam spustit na hostiteli."""
    krok("Steam")
    koreny = najdi_steam()
    if not koreny:
        pozor("Nenašel jsem Steam (složku userdata). Spusť Steam aspoň jednou a přihlas se.")
        return False
    if len(koreny) > 1:
        i = vyber("Našel jsem víc instalací Steamu. Kterou použít?",
                  [f"{k}{' (Flatpak)' if f else ''}" for k, f in koreny])
        koren, flatpak_steam = koreny[i]
    else:
        koren, flatpak_steam = koreny[0]
    ok(f"Steam: {koren}{' (Flatpak)' if flatpak_steam else ''}")

    ids, jmena, posledni = steam_uzivatele(koren)
    if not ids:
        pozor("Ve Steamu není žádný uživatel. Přihlas se do Steamu a spusť skript znovu.")
        return False
    if len(ids) > 1:
        vychozi = ids.index(posledni) + 1 if posledni in ids else 1
        i = vyber("Ke kterému Steam účtu hru přidat?",
                  [f"{jmena.get(u) or '(neznámé jméno)'} – {u}" for u in ids], vychozi)
        ucet = ids[i]
    else:
        ucet = ids[0]
    ok(f"Účet: {jmena.get(ucet) or ucet}")

    exe, argumenty = pro_steam
    if flatpak_steam:
        pozor("Flatpak Steam potřebuje oprávnění spouštět programy mimo sandbox (flatpak-spawn).")
        if ano("Povolit (flatpak override --user --talk-name=org.freedesktop.Flatpak)?"):
            subprocess.run(["flatpak", "override", "--user", "--talk-name=org.freedesktop.Flatpak", STEAM_FLATPAK])
        exe, argumenty = "/usr/bin/flatpak-spawn", f'--host "{exe}" {argumenty}'

    if not zavri_steam(flatpak_steam):
        pozor("Steam se nevypnul. Zavři ho a spusť skript znovu – hra už je připravená, jen ji použije.")
        return False

    config = os.path.join(koren, "userdata", ucet, "config")
    soubor = os.path.join(config, "shortcuts.vdf")
    os.makedirs(config, exist_ok=True)

    data = {}
    if os.path.exists(soubor):
        with open(soubor, "rb") as f:
            obsah = f.read()
        try:
            data = vdf_nacti(obsah)
        except Exception as e:
            pozor(f"Nerozumím souboru {soubor} ({e}). Nic jsem neměnil.")
            return False
        zaloha = f"{soubor}.zaloha-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(soubor, zaloha)
        ok(f"Záloha: {zaloha}")

    klic_zkratek = next((k for k in data if k.lower() == "shortcuts"), "shortcuts")
    zkratky = data.setdefault(klic_zkratek, {})

    exe_pole = f'"{exe}"'
    appid = zlib.crc32((exe_pole + program).encode("utf-8")) | 0x80000000

    ikona = os.path.join(config, "grid", f"{appid}_icon.png")
    if vytahni_ikonu(exe_hry, ikona):
        ok("Ikona vytažená z .exe")
    else:
        ikona = ""

    novy = {
        "appid": appid,
        "AppName": program,
        "Exe": exe_pole,
        "StartDir": f'"{koren if flatpak_steam else slozka_hry}"',
        "icon": ikona,
        "ShortcutPath": "",
        "LaunchOptions": argumenty,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {"0": stitek},
    }

    stary_klic = next(
        (k for k, v in zkratky.items()
         if isinstance(v, dict) and str(_ziskej(v, "AppName", "")).lower() == program.lower()),
        None,
    )
    if stary_klic is not None:
        if not ano(f"Ve Steamu už je hra „{program}“. Přepsat?"):
            pozor("Steam nechávám beze změny.")
            return False
        novy["LastPlayTime"] = _ziskej(zkratky[stary_klic], "LastPlayTime", 0)
        zkratky[stary_klic] = novy
    else:
        cisla = [int(k) for k in zkratky if k.isdigit()]
        zkratky[str(max(cisla) + 1 if cisla else 0)] = novy

    docasny = soubor + ".novy"
    with open(docasny, "wb") as f:
        f.write(vdf_zapis(data))
    os.replace(docasny, soubor)
    ok(f"Přidáno do Steamu: {program}")
    print(f"     Spouští: {exe} {argumenty}")
    return True


# --------------------------------------------------------------------------
# hlavní průběh
# --------------------------------------------------------------------------

def main():
    global PRESKOCIT_INNOEXTRACT
    argumenty = sys.argv[1:]
    predvoleny = None
    for prepinac in ("--heroic", "--bottles"):
        if prepinac in argumenty:
            argumenty.remove(prepinac)
            predvoleny = prepinac[2:]
    if "--wine" in argumenty:
        argumenty.remove("--wine")
        PRESKOCIT_INNOEXTRACT = True

    print(_c("1", "Windows hra  →  Bottles nebo Heroic  →  Steam"))

    # 1) přes co hru spouštět
    krok("Spouštěč")
    spoustec = vyber_spoustec(predvoleny)
    ok("Bottles" if spoustec == "bottles" else "Heroic")

    # 2) složka a .exe
    krok("Hra")
    zadani = argumenty[0] if argumenty else vyber_hru()
    slozka, exe = najdi_slozku(zadani)
    if not slozka:
        hledano = ", ".join(SLOZKY_S_HRAMI)
        konec(f"Složku „{zadani}“ jsem nenašel (zkoušel jsem ji jako cestu a v: {hledano}).")
    ok(f"Složka: {slozka}")
    if not exe:
        exe = vyber_exe(slozka)
    ok(f"Spouštěcí soubor: {os.path.relpath(exe, slozka)}")

    instalator = None
    if je_instalator(exe) and ano("Tohle vypadá jako instalátor. Nejdřív z něj hru nainstalovat?"):
        instalator = exe

    program = nazev_programu(zeptej("Název hry (takhle se ukáže ve Steamu)",
                                    nazev_programu(os.path.basename(slozka)) or "Hra")) or "Hra"

    # 3) Bottles nebo Heroic (u instalátoru včetně instalace)
    if spoustec == "bottles":
        exe, slozka, pro_steam, terminal = pres_bottles(program, slozka, exe, instalator)
    else:
        exe, slozka, pro_steam, terminal = pres_heroic(program, slozka, exe, instalator)

    # 4) Steam
    ve_steamu = False
    if ano("Přidat hru do Steamu?"):
        ve_steamu = pridej_do_steamu(program, exe, slozka, pro_steam,
                                     "Bottles" if spoustec == "bottles" else "Heroic")

    # shrnutí
    krok("Hotovo")
    print(f"  Hra: {program}")
    print(f"  Soubor hry: {exe}")
    print(f"  Spuštění z terminálu:\n    {terminal}")
    if ve_steamu:
        print("  Spusť Steam – hra bude v knihovně. Ve Vlastnostech → Kompatibilita NEZAPÍNEJ Proton,")
        print(f"  hru už spouští {'Bottles' if spoustec == 'bottles' else 'Heroic'}.")
    if spoustec == "bottles":
        print("  Když hra nejde: otevři bottle v Bottles → Dependencies (vcredist, d3dx9, dotnet...)")
        print("  a v Settings zkus jiný runner nebo vypni/zapni DXVK a VKD3D.")
    else:
        print("  Když hra nejde: v Heroicu otevři hru → Settings (Wine/Proton verze, DXVK...),")
        print("  knihovny doinstaluješ přes Winetricks ve stejném menu.")
        print("  Hru do Steamu nepřidávej ještě jednou přes Heroic („Add to Steam“), byla by tam dvakrát.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Přerušeno.")
        sys.exit(130)
