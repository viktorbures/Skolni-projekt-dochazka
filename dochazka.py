#!/usr/bin/env python3
"""Analýza docházky – konzolová aplikace pro vedení firmy.

Spuštění:
    python dochazka.py dochazka.csv

Aplikace načte export z docházkového terminálu (CSV se středníky),
spočítá odpracovanou dobu, najde pozdní příchody a neobvyklé směny,
připraví slovní hodnocení a výsledky uloží do JSON.
Používá standardní knihovnu; volitelně balíček rich pro hezčí výstup.
"""

import csv
import json
import statistics
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime

# Balíček rich je volitelný – bez něj aplikace běží v prostém textovém režimu.
try:
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    KONZOLE = Console(highlight=False)
    MAME_RICH = True
except ImportError:
    KONZOLE = None
    MAME_RICH = False

FORMAT_CASU = "%Y-%m-%d %H:%M"
FORMAT_DATA = "%Y-%m-%d"
DNY_TYDNE = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]

# Limity, které může uživatel měnit v menu (položka 12).
VYCHOZI_NASTAVENI = {
    "pozdni_limit": "08:00",   # příchod po tomto čase = pozdní příchod
    "kratka_smena": 4.0,       # směna kratší než X hodin = podezřelá
    "dlouha_smena": 11.0,      # směna delší než X hodin = podezřelá
}


# ----------------------------------------------------------------------
# Pomocné funkce (řetězce, čas, formátování)
# ----------------------------------------------------------------------
def bez_diakritiky(text):
    """Převede text na malá písmena bez diakritiky (pro vyhledávání)."""
    rozlozeny = unicodedata.normalize("NFKD", text)
    return "".join(z for z in rozlozeny if not unicodedata.combining(z)).lower().strip()


def formatuj_hodiny(hodiny):
    """8.25 -> '8 h 15 min'."""
    minuty = round(hodiny * 60)
    return f"{minuty // 60} h {minuty % 60:02d} min"


def formatuj_minuty(minuty):
    """Minuty od půlnoci -> 'HH:MM'."""
    minuty = round(minuty)
    return f"{minuty // 60:02d}:{minuty % 60:02d}"


def minuty_od_pulnoci(cas):
    return cas.hour * 60 + cas.minute


def limit_v_minutach(nastaveni):
    hodiny, minuty = nastaveni["pozdni_limit"].split(":")
    return int(hodiny) * 60 + int(minuty)


def je_pozdni(zaznam, nastaveni):
    return minuty_od_pulnoci(zaznam["prichod"]) > limit_v_minutach(nastaveni)


def je_neobvykla(zaznam, nastaveni):
    return (zaznam["hodiny"] < nastaveni["kratka_smena"]
            or zaznam["hodiny"] > nastaveni["dlouha_smena"])


def tiskni_tabulku(hlavicky, radky):
    """Vypíše přehlednou tabulku (rich, nebo prostý text)."""
    radky = [[str(bunka) for bunka in radek] for radek in radky]
    if MAME_RICH:
        tabulka = Table(box=box.SIMPLE_HEAD, header_style="bold cyan")
        for hlavicka in hlavicky:
            tabulka.add_column(hlavicka)
        for radek in radky:
            tabulka.add_row(*[Text(bunka) for bunka in radek])
        KONZOLE.print(tabulka)
        return
    sirky = [len(h) for h in hlavicky]
    for radek in radky:
        for i, bunka in enumerate(radek):
            sirky[i] = max(sirky[i], len(bunka))
    print("  ".join(h.ljust(sirky[i]) for i, h in enumerate(hlavicky)))
    print("  ".join("-" * s for s in sirky))
    for radek in radky:
        print("  ".join(b.ljust(sirky[i]) for i, b in enumerate(radek)))


def nadpis(text):
    print()
    if MAME_RICH:
        KONZOLE.print(Panel(Text(text, justify="center", style="bold"),
                            width=60, border_style="cyan"))
    else:
        print("=" * 60)
        print(text.center(60))
        print("=" * 60)


def barva(text, styl):
    """Obarví text v rich; v prostém režimu ho nechá beze změny."""
    return f"[{styl}]{text}[/]" if MAME_RICH else text


def vypis(text):
    """Vypíše řádek s případnými rich značkami (jinak běžný print)."""
    if MAME_RICH:
        KONZOLE.print(text)
    else:
        print(text)


def pockej():
    input("\nPokračujte klávesou Enter...")


# ----------------------------------------------------------------------
# Načtení a kontrola dat
# ----------------------------------------------------------------------
def cti_radky(cesta):
    """Přečte soubor v UTF-8 (s BOM) nebo v CP1250 (české Windows)."""
    for kodovani in ("utf-8-sig", "cp1250"):
        try:
            with open(cesta, encoding=kodovani, newline="") as soubor:
                return soubor.read().splitlines()
        except UnicodeDecodeError:
            continue
    raise ValueError("Soubor má neznámé kódování.")


def nacti_csv(cesta):
    """Načte CSV. Vrací (seznam záznamů, seznam chyb)."""
    radky = cti_radky(cesta)
    if not radky:
        raise ValueError("Soubor je prázdný.")

    cteni = csv.reader(radky, delimiter=";")
    hlavicka = [bez_diakritiky(s) for s in next(cteni)]
    potrebne = ["cislo cipu", "zamestnanec", "prichod", "odchod"]
    chybi = [s for s in potrebne if s not in hlavicka]
    if chybi:
        raise ValueError("V hlavičce chybí sloupce: " + ", ".join(chybi))
    poz = {nazev: hlavicka.index(nazev) for nazev in potrebne}

    zaznamy, chyby = [], []
    for cislo_radku, radek in enumerate(cteni, start=2):
        if not any(bunka.strip() for bunka in radek):
            continue  # prázdný řádek
        try:
            cip = radek[poz["cislo cipu"]].strip()
            jmeno = radek[poz["zamestnanec"]].strip()
            prichod = datetime.strptime(radek[poz["prichod"]].strip(), FORMAT_CASU)
            odchod = datetime.strptime(radek[poz["odchod"]].strip(), FORMAT_CASU)
        except (IndexError, ValueError):
            chyby.append((cislo_radku, "chybný nebo chybějící údaj"))
            continue
        if not cip or not jmeno:
            chyby.append((cislo_radku, "chybí číslo čipu nebo jméno"))
        elif odchod <= prichod:
            chyby.append((cislo_radku, "odchod není později než příchod"))
        else:
            zaznamy.append({
                "cip": cip,
                "jmeno": jmeno,
                "prichod": prichod,
                "odchod": odchod,
                "hodiny": (odchod - prichod).total_seconds() / 3600,
            })
    return zaznamy, chyby


def kontrola_dat(zaznamy):
    """Hledá logické nesrovnalosti: různá jména u čipu, překrývající se směny."""
    problemy = []
    jmena = defaultdict(set)
    podle_cipu = defaultdict(list)
    for z in zaznamy:
        jmena[z["cip"]].add(z["jmeno"])
        podle_cipu[z["cip"]].append(z)

    for cip, mnozina in jmena.items():
        if len(mnozina) > 1:
            problemy.append(f"Čip {cip} je veden pod více jmény: {', '.join(sorted(mnozina))}")

    for cip, seznam in podle_cipu.items():
        seznam.sort(key=lambda z: z["prichod"])
        for predchozi, dalsi in zip(seznam, seznam[1:]):
            if dalsi["prichod"] < predchozi["odchod"]:
                problemy.append(
                    f"{predchozi['jmeno']}: překrývající se záznamy "
                    f"({predchozi['prichod'].strftime(FORMAT_CASU)} a "
                    f"{dalsi['prichod'].strftime(FORMAT_CASU)})")
    return problemy


# ----------------------------------------------------------------------
# Výpočty a statistiky
# ----------------------------------------------------------------------
def souhrn_zamestnancu(zaznamy, nastaveni):
    """Statistiky za každého zaměstnance (seznam slovníků)."""
    skupiny = defaultdict(list)
    for z in zaznamy:
        skupiny[z["cip"]].append(z)

    vysledek = []
    for cip, seznam in skupiny.items():
        hodiny = [z["hodiny"] for z in seznam]
        pozdni = sum(1 for z in seznam if je_pozdni(z, nastaveni))
        neobvykle = sum(1 for z in seznam if je_neobvykla(z, nastaveni))
        spatne = sum(1 for z in seznam
                     if je_pozdni(z, nastaveni) or je_neobvykla(z, nastaveni))
        prumer_prichodu = statistics.mean(minuty_od_pulnoci(z["prichod"]) for z in seznam)
        vysledek.append({
            "cip": cip,
            "jmeno": seznam[0]["jmeno"],
            "pocet_dni": len({z["prichod"].date() for z in seznam}),
            "pocet_smen": len(seznam),
            "celkem": sum(hodiny),
            "prumer": statistics.mean(hodiny),
            "min": min(hodiny),
            "max": max(hodiny),
            "prumerny_prichod": formatuj_minuty(prumer_prichodu),
            "pozdni": pozdni,
            "neobvykle": neobvykle,
            # vlastní ukazatel: podíl směn bez pozdního příchodu a bez anomálie
            "spolehlivost": 100 * (len(seznam) - spatne) / len(seznam),
        })
    return vysledek


def statistika_dne(zaznamy, den, nastaveni):
    """Statistika jednoho dne; vrací None, pokud pro den nejsou data."""
    seznam = [z for z in zaznamy if z["prichod"].date() == den]
    if not seznam:
        return None
    prvni = min(seznam, key=lambda z: z["prichod"])
    posledni = max(seznam, key=lambda z: z["odchod"])
    return {
        "datum": den.strftime(FORMAT_DATA),
        "den_v_tydnu": DNY_TYDNE[den.weekday()],
        "pocet_pritomnych": len({z["cip"] for z in seznam}),
        "prvni_prichod": {"zaměstnanec": prvni["jmeno"],
                          "čas": prvni["prichod"].strftime("%H:%M")},
        "posledni_odchod": {"zaměstnanec": posledni["jmeno"],
                            "čas": posledni["odchod"].strftime("%H:%M")},
        "celkem_hodin": round(sum(z["hodiny"] for z in seznam), 2),
        "prumerna_smena": round(statistics.mean(z["hodiny"] for z in seznam), 2),
        "pozdni_prichody": sum(1 for z in seznam if je_pozdni(z, nastaveni)),
    }


def statistika_dnu_tydne(zaznamy, nastaveni):
    """Průměrný příchod, pozdní příchody a délka směny podle dne v týdnu."""
    skupiny = defaultdict(list)
    for z in zaznamy:
        skupiny[z["prichod"].weekday()].append(z)
    vysledek = []
    for index in sorted(skupiny):
        seznam = skupiny[index]
        vysledek.append({
            "index": index,
            "den": DNY_TYDNE[index],
            "smeny": len(seznam),
            "prumerny_prichod_min": statistics.mean(minuty_od_pulnoci(z["prichod"]) for z in seznam),
            "pozdni": sum(1 for z in seznam if je_pozdni(z, nastaveni)),
            "prumer_hodin": statistics.mean(z["hodiny"] for z in seznam),
        })
    return vysledek


def pozdni_prichody(zaznamy, nastaveni):
    """Seznam pozdních příchodů včetně zpoždění v minutách."""
    limit = limit_v_minutach(nastaveni)
    vysledek = []
    for z in zaznamy:
        if je_pozdni(z, nastaveni):
            vysledek.append({**z, "zpozdeni": minuty_od_pulnoci(z["prichod"]) - limit})
    return sorted(vysledek, key=lambda z: z["zpozdeni"], reverse=True)


def slovni_hodnoceni(zaznamy, nastaveni):
    """Automatické slovní hodnocení – seznam vět pro vedení firmy."""
    souhrn = souhrn_zamestnancu(zaznamy, nastaveni)
    dny = statistika_dnu_tydne(zaznamy, nastaveni)
    pozdni = pozdni_prichody(zaznamy, nastaveni)
    neobvykle = [z for z in zaznamy if je_neobvykla(z, nastaveni)]
    vety = []

    nejvic = max(souhrn, key=lambda s: s["celkem"])
    vety.append(f"Nejvíce hodin odpracoval {nejvic['jmeno']} "
                f"({nejvic['celkem']:.1f} h za {nejvic['pocet_dni']} dní).")

    nejdelsi = max(souhrn, key=lambda s: s["prumer"])
    vety.append(f"Nejdelší průměrnou směnu má {nejdelsi['jmeno']} ({formatuj_hodiny(nejdelsi['prumer'])}).")

    podil = 100 * len(pozdni) / len(zaznamy)
    vety.append(f"Pozdních příchodů (po {nastaveni['pozdni_limit']}) je {len(pozdni)} "
                f"z {len(zaznamy)} směn, tedy {podil:.1f} %.")

    nejcasteji = max(souhrn, key=lambda s: s["pozdni"])
    if nejcasteji["pozdni"] > 0:
        vety.append(f"Nejčastěji chodí pozdě {nejcasteji['jmeno']} "
                    f"({nejcasteji['pozdni']}× z {nejcasteji['pocet_smen']} směn).")

    nejlepsi = max(souhrn, key=lambda s: (s["spolehlivost"], s["celkem"]))
    vety.append(f"Nejspolehlivější je {nejlepsi['jmeno']} "
                f"(index spolehlivosti {nejlepsi['spolehlivost']:.0f} %).")

    if dny:
        nejhorsi_den = max(dny, key=lambda d: d["prumerny_prichod_min"])
        vety.append(f"Nejpozději se chodí v den „{nejhorsi_den['den']}“ "
                    f"(průměrný příchod {formatuj_minuty(nejhorsi_den['prumerny_prichod_min'])}).")

    if neobvykle:
        vety.append(f"Bylo nalezeno {len(neobvykle)} neobvykle krátkých či dlouhých směn "
                    "– doporučuji ověřit, zda nejde o chybu čipu nebo o nepřítomnost.")
    else:
        vety.append("Žádné neobvyklé směny nebyly nalezeny.")

    if podil > 15:
        vety.append("Doporučení: podíl pozdních příchodů je vysoký – zvažte pružnou "
                    "pracovní dobu nebo posunutí začátku směny.")
    elif podil > 0:
        vety.append("Doporučení: pozdní příchody jsou ojedinělé, stačí individuální rozhovor.")
    else:
        vety.append("Doporučení: docházka je bez pozdních příchodů, není třeba zasahovat.")
    return vety


# ----------------------------------------------------------------------
# Vstup od uživatele
# ----------------------------------------------------------------------
def zadej_datum(zaznamy):
    dny = sorted({z["prichod"].date() for z in zaznamy})
    print(f"Dostupná data: {dny[0]} až {dny[-1]} ({len(dny)} dní)")
    while True:
        text = input("Zadejte datum RRRR-MM-DD (Enter = zpět): ").strip()
        if not text:
            return None
        try:
            den = datetime.strptime(text, FORMAT_DATA).date()
        except ValueError:
            print("Neplatný formát data.")
            continue
        if den in dny:
            return den
        print("Pro tento den nejsou žádná data.")


def zadej_cislo(otazka, aktualni, minimum, maximum):
    """Načte desetinné číslo; prázdný vstup ponechá původní hodnotu."""
    while True:
        text = input(f"{otazka} [{aktualni}]: ").strip().replace(",", ".")
        if not text:
            return aktualni
        try:
            hodnota = float(text)
        except ValueError:
            print("Zadejte číslo.")
            continue
        if minimum <= hodnota <= maximum:
            return hodnota
        print(f"Hodnota musí být v rozsahu {minimum} až {maximum}.")


# ----------------------------------------------------------------------
# Položky menu
# ----------------------------------------------------------------------
def menu_prehled(ctx):
    nadpis("PŘEHLED DAT A KONTROLA KVALITY")
    zaznamy = ctx["zaznamy"]
    od = min(z["prichod"] for z in zaznamy)
    do = max(z["odchod"] for z in zaznamy)
    print(f"Soubor:              {ctx['cesta']}")
    print(f"Platných záznamů:    {len(zaznamy)}")
    print(f"Zaměstnanců:         {len({z['cip'] for z in zaznamy})}")
    print(f"Sledované období:    {od:%d.%m.%Y} – {do:%d.%m.%Y}")
    print(f"Dní s daty:          {len({z['prichod'].date() for z in zaznamy})}")
    print(f"Celkem odpracováno:  {sum(z['hodiny'] for z in zaznamy):.1f} h")
    print(f"Vadných řádků:       {len(ctx['chyby'])}")
    for radek, duvod in ctx["chyby"][:10]:
        print(f"   řádek {radek}: {duvod}")
    problemy = kontrola_dat(zaznamy)
    print(f"Logických problémů:  {len(problemy)}")
    for problem in problemy[:10]:
        print("   -", problem)


def menu_souhrn(ctx):
    nadpis("SOUHRN PO ZAMĚSTNANCÍCH")
    souhrn = sorted(souhrn_zamestnancu(ctx["zaznamy"], ctx["nastaveni"]), key=lambda s: s["jmeno"])
    tiskni_tabulku(
        ["Čip", "Zaměstnanec", "Dní", "Celkem h", "Průměr h", "Min h", "Max h", "Ø příchod"],
        [[s["cip"], s["jmeno"], s["pocet_dni"], f"{s['celkem']:.2f}", f"{s['prumer']:.2f}",
          f"{s['min']:.2f}", f"{s['max']:.2f}", s["prumerny_prichod"]] for s in souhrn])


def menu_zebricek(ctx):
    nadpis("ŽEBŘÍČEK PODLE ODPRACOVANÉ DOBY")
    souhrn = sorted(souhrn_zamestnancu(ctx["zaznamy"], ctx["nastaveni"]),
                    key=lambda s: s["celkem"], reverse=True)
    nejvic = souhrn[0]["celkem"]
    for poradi, s in enumerate(souhrn, start=1):
        sloupec = "█" * round(25 * s["celkem"] / nejvic)
        vypis(f"{poradi:>2}. {s['jmeno']:<22} {s['celkem']:>7.2f} h  "
              f"{barva(sloupec, 'green')}")
    print("\nNejvyšší průměrná směna:")
    nejlepsi = max(souhrn, key=lambda s: s["prumer"])
    print(f"   {nejlepsi['jmeno']} – {formatuj_hodiny(nejlepsi['prumer'])}")


def menu_hledat(ctx):
    nadpis("VYHLEDÁNÍ ZAMĚSTNANCE")
    dotaz = input("Zadejte jméno (i část, bez diakritiky) nebo číslo čipu: ").strip()
    if not dotaz:
        return
    hledany = bez_diakritiky(dotaz)
    nalezene = [z for z in ctx["zaznamy"]
                if hledany in bez_diakritiky(z["jmeno"]) or dotaz == z["cip"]]
    if not nalezene:
        print("Nikdo nenalezen.")
        return
    nalezene.sort(key=lambda z: (z["jmeno"], z["prichod"]))
    cipy = sorted({z["cip"] for z in nalezene})
    souhrn = {s["cip"]: s for s in souhrn_zamestnancu(nalezene, ctx["nastaveni"])}
    for cip in cipy:
        s = souhrn[cip]
        print(f"\n{s['jmeno']} (čip {cip}): {s['pocet_dni']} dní, {s['celkem']:.2f} h celkem, "
              f"průměr {formatuj_hodiny(s['prumer'])}, spolehlivost {s['spolehlivost']:.0f} %")
        radky = []
        for z in nalezene:
            if z["cip"] != cip:
                continue
            poznamky = []
            if je_pozdni(z, ctx["nastaveni"]):
                poznamky.append("pozdní příchod")
            if je_neobvykla(z, ctx["nastaveni"]):
                poznamky.append("neobvyklá délka")
            radky.append([z["prichod"].strftime("%d.%m."), DNY_TYDNE[z["prichod"].weekday()],
                          z["prichod"].strftime("%H:%M"), z["odchod"].strftime("%H:%M"),
                          f"{z['hodiny']:.2f}", ", ".join(poznamky)])
        tiskni_tabulku(["Datum", "Den", "Příchod", "Odchod", "Hodin", "Poznámka"], radky)


def menu_den(ctx):
    nadpis("STATISTIKA VYBRANÉHO DNE")
    den = zadej_datum(ctx["zaznamy"])
    if den is None:
        return
    s = statistika_dne(ctx["zaznamy"], den, ctx["nastaveni"])
    print(f"\n{s['den_v_tydnu'].capitalize()} {den:%d.%m.%Y}")
    print(f"Přítomných zaměstnanců: {s['pocet_pritomnych']}")
    print(f"První příchod:   {s['prvni_prichod']['čas']}  ({s['prvni_prichod']['zaměstnanec']})")
    print(f"Poslední odchod: {s['posledni_odchod']['čas']}  ({s['posledni_odchod']['zaměstnanec']})")
    print(f"Celkem hodin:    {s['celkem_hodin']:.2f} (průměrná směna {s['prumerna_smena']:.2f} h)")
    print(f"Pozdních příchodů: {s['pozdni_prichody']}")


def menu_extremy(ctx):
    nadpis("NEJKRATŠÍ A NEJDELŠÍ SMĚNY")
    seznam = sorted(ctx["zaznamy"], key=lambda z: z["hodiny"])

    def radky(vyber):
        return [[z["jmeno"], z["prichod"].strftime("%d.%m.%Y"), z["prichod"].strftime("%H:%M"),
                 z["odchod"].strftime("%H:%M"), formatuj_hodiny(z["hodiny"])] for z in vyber]

    hlavicka = ["Zaměstnanec", "Datum", "Příchod", "Odchod", "Délka"]
    print("5 nejkratších směn:")
    tiskni_tabulku(hlavicka, radky(seznam[:5]))
    print("\n5 nejdelších směn:")
    tiskni_tabulku(hlavicka, radky(seznam[:-6:-1]))


def menu_pozdni(ctx):
    nadpis(f"POZDNÍ PŘÍCHODY (po {ctx['nastaveni']['pozdni_limit']})")
    pozdni = pozdni_prichody(ctx["zaznamy"], ctx["nastaveni"])
    if not pozdni:
        print("Žádné pozdní příchody.")
        return
    souhrn = sorted(souhrn_zamestnancu(ctx["zaznamy"], ctx["nastaveni"]),
                    key=lambda s: s["pozdni"], reverse=True)
    print("Podle zaměstnanců:")
    tiskni_tabulku(["Zaměstnanec", "Pozdě", "Směn", "Podíl"],
                   [[s["jmeno"], s["pozdni"], s["pocet_smen"],
                     f"{100 * s['pozdni'] / s['pocet_smen']:.0f} %"] for s in souhrn if s["pozdni"]])
    print("\n10 největších zpoždění:")
    tiskni_tabulku(["Zaměstnanec", "Datum", "Příchod", "Zpoždění"],
                   [[z["jmeno"], z["prichod"].strftime("%d.%m.%Y"), z["prichod"].strftime("%H:%M"),
                     f"{z['zpozdeni']} min"] for z in pozdni[:10]])


def menu_neobvykle(ctx):
    nastaveni = ctx["nastaveni"]
    nadpis("NEOBVYKLÉ SMĚNY")
    print(f"Kratší než {nastaveni['kratka_smena']} h nebo delší než {nastaveni['dlouha_smena']} h\n")
    nalezene = [z for z in ctx["zaznamy"] if je_neobvykla(z, nastaveni)]
    if not nalezene:
        print("Žádné neobvyklé směny nebyly nalezeny.")
        return
    nalezene.sort(key=lambda z: z["hodiny"])
    tiskni_tabulku(["Zaměstnanec", "Datum", "Příchod", "Odchod", "Délka", "Typ"],
                   [[z["jmeno"], z["prichod"].strftime("%d.%m.%Y"), z["prichod"].strftime("%H:%M"),
                     z["odchod"].strftime("%H:%M"), formatuj_hodiny(z["hodiny"]),
                     "krátká" if z["hodiny"] < nastaveni["kratka_smena"] else "dlouhá"]
                    for z in nalezene])


def menu_pritomnost(ctx):
    nadpis("KDO BYL PŘÍTOMEN V ZADANÝ ČAS")
    text = input("Zadejte čas RRRR-MM-DD HH:MM (Enter = zpět): ").strip()
    if not text:
        return
    try:
        okamzik = datetime.strptime(text, FORMAT_CASU)
    except ValueError:
        print("Neplatný formát času.")
        return
    pritomni = sorted((z for z in ctx["zaznamy"] if z["prichod"] <= okamzik < z["odchod"]),
                      key=lambda z: z["jmeno"])
    print(f"\nV {okamzik:%d.%m.%Y %H:%M} bylo na pracovišti {len(pritomni)} lidí.")
    for z in pritomni:
        print(f"   {z['jmeno']:<22} od {z['prichod']:%H:%M} do {z['odchod']:%H:%M}")


def menu_dny_tydne(ctx):
    nadpis("PŘÍCHODY PODLE DNE V TÝDNU")
    dny = statistika_dnu_tydne(ctx["zaznamy"], ctx["nastaveni"])
    tiskni_tabulku(["Den", "Směn", "Ø příchod", "Pozdě", "Ø délka směny"],
                   [[d["den"], d["smeny"], formatuj_minuty(d["prumerny_prichod_min"]),
                     d["pozdni"], formatuj_hodiny(d["prumer_hodin"])] for d in dny])
    nejpozdeji = max(dny, key=lambda d: d["prumerny_prichod_min"])
    print(f"\nNejpozději se chodí: {nejpozdeji['den']}.")


def menu_hodnoceni(ctx):
    nadpis("SLOVNÍ HODNOCENÍ PRO VEDENÍ")
    for veta in slovni_hodnoceni(ctx["zaznamy"], ctx["nastaveni"]):
        vypis(f" {barva('•', 'cyan')} {veta}")


def menu_nastaveni(ctx):
    nadpis("NASTAVENÍ LIMITŮ")
    nastaveni = ctx["nastaveni"]
    while True:
        text = input(f"Limit pozdního příchodu HH:MM [{nastaveni['pozdni_limit']}]: ").strip()
        if not text:
            break
        try:
            datetime.strptime(text, "%H:%M")
            nastaveni["pozdni_limit"] = text
            break
        except ValueError:
            print("Zadejte čas ve tvaru HH:MM.")
    nastaveni["kratka_smena"] = zadej_cislo("Směna kratší než (hodin)", nastaveni["kratka_smena"], 0, 24)
    nastaveni["dlouha_smena"] = zadej_cislo("Směna delší než (hodin)", nastaveni["dlouha_smena"], 1, 24)
    print("Nastavení uloženo.")


def sestav_vystup(ctx):
    """Sestaví slovník s odvozenými daty pro JSON."""
    zaznamy, nastaveni = ctx["zaznamy"], ctx["nastaveni"]
    zamestnanci = [{
        "číslo čipu": s["cip"],
        "zaměstnanec": s["jmeno"],
        "počet_dní": s["pocet_dni"],
        "celkem_hodin": round(s["celkem"], 2),
        "průměrná_směna": round(s["prumer"], 2),
        "nejkratší_směna": round(s["min"], 2),
        "nejdelší_směna": round(s["max"], 2),
        "průměrný_příchod": s["prumerny_prichod"],
        "pozdní_příchody": s["pozdni"],
        "neobvyklé_směny": s["neobvykle"],
        "index_spolehlivosti": round(s["spolehlivost"], 1),
    } for s in sorted(souhrn_zamestnancu(zaznamy, nastaveni), key=lambda s: s["celkem"], reverse=True)]

    dny = sorted({z["prichod"].date() for z in zaznamy})
    return {
        "vygenerováno": datetime.now().strftime(FORMAT_CASU),
        "zdrojový_soubor": ctx["cesta"],
        "nastavení": nastaveni,
        "období": {"od": dny[0].strftime(FORMAT_DATA), "do": dny[-1].strftime(FORMAT_DATA)},
        "celkem": {
            "záznamů": len(zaznamy),
            "zaměstnanců": len(zamestnanci),
            "odpracováno_hodin": round(sum(z["hodiny"] for z in zaznamy), 2),
            "vadných_řádků": len(ctx["chyby"]),
        },
        "zaměstnanci": zamestnanci,
        "dny": [statistika_dne(zaznamy, den, nastaveni) for den in dny],
        "pozdní_příchody": [{
            "zaměstnanec": z["jmeno"], "datum": z["prichod"].strftime(FORMAT_DATA),
            "příchod": z["prichod"].strftime("%H:%M"), "zpoždění_min": z["zpozdeni"],
        } for z in pozdni_prichody(zaznamy, nastaveni)],
        "neobvyklé_směny": [{
            "zaměstnanec": z["jmeno"], "datum": z["prichod"].strftime(FORMAT_DATA),
            "délka_hodin": round(z["hodiny"], 2),
        } for z in zaznamy if je_neobvykla(z, nastaveni)],
        "slovní_hodnocení": slovni_hodnoceni(zaznamy, nastaveni),
    }


def menu_export(ctx):
    nadpis("EXPORT DO JSON")
    nazev = input("Název výstupního souboru [vysledek.json]: ").strip() or "vysledek.json"
    if not nazev.lower().endswith(".json"):
        nazev += ".json"
    try:
        with open(nazev, "w", encoding="utf-8") as soubor:
            json.dump(sestav_vystup(ctx), soubor, ensure_ascii=False, indent=4)
    except OSError as chyba:
        print("Soubor se nepodařilo uložit:", chyba)
        return
    print(f"Výsledky byly uloženy do souboru {nazev}.")


MENU = [
    ("1", "Přehled dat a kontrola kvality", menu_prehled),
    ("2", "Souhrn po zaměstnancích", menu_souhrn),
    ("3", "Žebříček podle odpracované doby", menu_zebricek),
    ("4", "Vyhledání zaměstnance (jméno / čip)", menu_hledat),
    ("5", "Statistika vybraného dne", menu_den),
    ("6", "Nejkratší a nejdelší směny", menu_extremy),
    ("7", "Pozdní příchody", menu_pozdni),
    ("8", "Neobvykle krátké / dlouhé směny", menu_neobvykle),
    ("9", "Kdo byl přítomen v zadaný čas", menu_pritomnost),
    ("10", "Příchody podle dne v týdnu", menu_dny_tydne),
    ("11", "Slovní hodnocení pro vedení", menu_hodnoceni),
    ("12", "Nastavení limitů", menu_nastaveni),
    ("13", "Export do JSON", menu_export),
]


def zobraz_menu():
    nadpis("ANALÝZA DOCHÁZKY")
    for kod, popis, _ in MENU:
        print(f"{kod:>3} - {popis}")
    print("  0 - Konec")


def main():
    if len(sys.argv) < 2:
        print("Použití: python dochazka.py dochazka.csv")
        return 1
    cesta = sys.argv[1]
    try:
        zaznamy, chyby = nacti_csv(cesta)
    except FileNotFoundError:
        print(f"Soubor '{cesta}' nebyl nalezen.")
        return 1
    except ValueError as chyba:
        print("Chyba ve vstupním souboru:", chyba)
        return 1
    if not zaznamy:
        print("Soubor neobsahuje žádný platný záznam.")
        return 1

    ctx = {"zaznamy": zaznamy, "chyby": chyby, "cesta": cesta,
           "nastaveni": dict(VYCHOZI_NASTAVENI)}
    akce = {kod: funkce for kod, _, funkce in MENU}
    print(f"Načteno {len(zaznamy)} záznamů, vadných řádků: {len(chyby)}.")

    try:
        while True:
            zobraz_menu()
            volba = input("\nVaše volba: ").strip()
            if volba == "0":
                print("Na shledanou.")
                return 0
            if volba in akce:
                akce[volba](ctx)
                pockej()
            else:
                print("Neplatná volba, zkuste to znovu.")
    except (EOFError, KeyboardInterrupt):
        print("\nUkončeno.")
        return 0


if __name__ == "__main__":
    sys.exit(main())