"""Realistische Beispieldaten (deterministisch, ``seed``).

Simuliert stündliche Snapshots für mehrere Auktionshäuser mit
* Random Walk + Trend je Item,
* Tageszeitmuster (abends teurer, nachts billiger),
* Wochentagsmuster (Raid-Reset Mittwoch EU: Verbrauchsgüter teurer),
* seltenen Spitzen/Einbrüchen (Ausreißer) und
* aktuellen „Deals“ (letzte Stunden deutlich unter Durchschnitt).

Pro Snapshot werden einzelne Auktionen erzeugt und über denselben
Aggregationscode wie die Live-Quelle verdichtet.
"""
from __future__ import annotations

import math
import random
import time

from .. import db
from ..aggregate import aggregate_listings
from . import ImportResult

SOURCE = "demo"
G, S = 10000, 100

# (id, name, qualität, klasse, klassenname, unterklasse, unterklassenname, icon, basispreis, tagesmenge, typ)
ITEMS = [
    # Handwerkswaren – Metall & Stein
    (2770, "Kupfererz", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ore_copper_01", 18 * S, 900, "mat"),
    (2771, "Zinnerz", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ore_tin_01", 30 * S, 600, "mat"),
    (2772, "Eisenerz", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ore_iron_01", 85 * S, 450, "mat"),
    (3858, "Mithrilerz", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ore_mithril_02", 1 * G + 20 * S, 380, "mat"),
    (10620, "Thoriumerz", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ore_thorium_02", 2 * G + 40 * S, 300, "mat"),
    (2840, "Kupferbarren", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ingot_02", 25 * S, 500, "mat"),
    (3575, "Eisenbarren", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ingot_iron", 1 * G + 5 * S, 260, "mat"),
    (12359, "Thoriumbarren", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_ingot_thorium", 3 * G + 10 * S, 220, "mat"),
    (12365, "Schwerer Stein", 1, 7, "Handwerkswaren", 7, "Metall & Stein", "inv_stone_10", 40 * S, 300, "mat"),
    (12808, "Essenz des Untodes", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_misc_powder_black", 4 * G, 60, "mat"),
    (7076, "Essenz der Erde", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_elemental_primal_earth", 3 * G + 50 * S, 80, "mat"),
    (7078, "Essenz des Feuers", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_elemental_primal_fire", 5 * G, 70, "mat"),
    (7080, "Essenz des Wassers", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_elemental_primal_water", 2 * G + 80 * S, 90, "mat"),
    (7082, "Essenz der Luft", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_elemental_primal_air", 6 * G, 40, "mat"),
    (12803, "Lebendige Essenz", 2, 7, "Handwerkswaren", 10, "Elementar", "inv_elemental_primal_life", 3 * G, 70, "mat"),
    # Stoff
    (2589, "Leinenstoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_linen_01", 12 * S, 1500, "mat"),
    (2592, "Wollstoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_wool_01", 22 * S, 1100, "mat"),
    (4306, "Seidenstoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_silk_01", 45 * S, 900, "mat"),
    (4338, "Magiestoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_mageweave_01", 70 * S, 800, "mat"),
    (14047, "Runenstoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_purple_01", 1 * G + 10 * S, 1200, "mat"),
    (14256, "Teufelsstoff", 1, 7, "Handwerkswaren", 5, "Stoff", "inv_fabric_felcloth_ebon", 9 * G, 40, "mat"),
    # Leder
    (2318, "Leichtes Leder", 1, 7, "Handwerkswaren", 6, "Leder", "inv_misc_leatherscrap_03", 15 * S, 900, "mat"),
    (2319, "Mittleres Leder", 1, 7, "Handwerkswaren", 6, "Leder", "inv_misc_leatherscrap_05", 30 * S, 700, "mat"),
    (4234, "Schweres Leder", 1, 7, "Handwerkswaren", 6, "Leder", "inv_misc_leatherscrap_07", 55 * S, 600, "mat"),
    (4304, "Dickes Leder", 1, 7, "Handwerkswaren", 6, "Leder", "inv_misc_leatherscrap_08", 90 * S, 500, "mat"),
    (8170, "Unverwüstliches Leder", 1, 7, "Handwerkswaren", 6, "Leder", "inv_misc_leatherscrap_02", 1 * G + 60 * S, 450, "mat"),
    # Kräuter
    (2447, "Friedensblume", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_flower_02", 10 * S, 700, "mat"),
    (765, "Silberblatt", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_10", 8 * S, 800, "mat"),
    (785, "Maguskönigskraut", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_07", 30 * S, 500, "mat"),
    (3820, "Würgetang", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_11", 40 * S, 400, "mat"),
    (3356, "Königsblut", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_03", 55 * S, 400, "mat"),
    (8836, "Arthas' Tränen", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_13", 70 * S, 250, "mat"),
    (13463, "Traumblatt", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_dreamfoil", 1 * G + 50 * S, 300, "mat"),
    (13465, "Bergsilbersalbei", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_mountainsilversage", 2 * G + 50 * S, 220, "mat"),
    (13467, "Pestblüte", 1, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_plaguebloom", 1 * G + 80 * S, 200, "mat"),
    (13468, "Schwarzer Lotus", 2, 7, "Handwerkswaren", 9, "Kräuter", "inv_misc_herb_blacklotus", 95 * G, 6, "rare"),
    # Verzauberkunst
    (10940, "Seltsamer Staub", 1, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_duststrange", 25 * S, 500, "mat"),
    (11176, "Traumstaub", 1, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_dustdream", 1 * G + 80 * S, 300, "mat"),
    (16204, "Illusionsstaub", 1, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_dustillusion", 3 * G + 20 * S, 400, "mat"),
    (16203, "Große ewige Essenz", 2, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_essenceeternallarge", 14 * G, 90, "mat"),
    (14344, "Großer glänzender Splitter", 3, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_shardbrilliantlarge", 6 * G + 50 * S, 120, "mat"),
    (20725, "Nexuskristall", 4, 7, "Handwerkswaren", 12, "Verzauberkunst", "inv_enchant_shardnexuslarge", 28 * G, 25, "rare"),
    # Kochzutaten
    (12208, "Zartes Wolfsfleisch", 1, 7, "Handwerkswaren", 8, "Kochzutaten", "inv_misc_food_14", 60 * S, 300, "mat"),
    (13888, "Dunkelgeschuppter Riffbarsch", 1, 7, "Handwerkswaren", 8, "Kochzutaten", "inv_misc_fish_19", 80 * S, 250, "mat"),
    (13754, "Roher Glossy-Mightfish", 1, 7, "Handwerkswaren", 8, "Kochzutaten", "inv_misc_fish_02", 45 * S, 200, "mat"),
    # Verbrauchbares – Tränke & Fläschchen
    (13444, "Erheblicher Manatrank", 1, 0, "Verbrauchbares", 1, "Trank", "inv_potion_76", 2 * G + 20 * S, 260, "consum"),
    (13446, "Erheblicher Heiltrank", 1, 0, "Verbrauchbares", 1, "Trank", "inv_potion_54", 1 * G + 60 * S, 300, "consum"),
    (3928, "Überragender Heiltrank", 1, 0, "Verbrauchbares", 1, "Trank", "inv_potion_53", 70 * S, 200, "consum"),
    (13457, "Großer Feuerschutztrank", 1, 0, "Verbrauchbares", 1, "Trank", "inv_potion_24", 6 * G, 160, "consum"),
    (20007, "Mageblood-Trank", 1, 0, "Verbrauchbares", 1, "Trank", "inv_potion_45", 3 * G + 50 * S, 120, "consum"),
    (13452, "Elixier des Mungos", 1, 0, "Verbrauchbares", 2, "Elixier", "inv_potion_32", 9 * G, 110, "consum"),
    (9206, "Elixier der Riesen", 1, 0, "Verbrauchbares", 2, "Elixier", "inv_potion_61", 5 * G + 50 * S, 120, "consum"),
    (13454, "Großes arkanes Elixier", 1, 0, "Verbrauchbares", 2, "Elixier", "inv_potion_25", 3 * G, 100, "consum"),
    (13510, "Fläschchen der Titanen", 1, 0, "Verbrauchbares", 3, "Fläschchen", "inv_potion_62", 85 * G, 14, "consum_rare"),
    (13511, "Fläschchen der destillierten Weisheit", 1, 0, "Verbrauchbares", 3, "Fläschchen", "inv_potion_97", 70 * G, 12, "consum_rare"),
    (13512, "Fläschchen der obersten Macht", 1, 0, "Verbrauchbares", 3, "Fläschchen", "inv_potion_41", 95 * G, 10, "consum_rare"),
    # Verbrauchbares – Essen, Verbände, Sprengstoff
    (13931, "Nachtflossensuppe", 1, 0, "Verbrauchbares", 5, "Essen & Trinken", "inv_drink_17", 1 * G + 40 * S, 140, "consum"),
    (13928, "Gegrillter Kalmar", 1, 0, "Verbrauchbares", 5, "Essen & Trinken", "inv_misc_fish_13", 2 * G + 10 * S, 130, "consum"),
    (20452, "Geräuchertes Wüstenfischfilet", 1, 0, "Verbrauchbares", 5, "Essen & Trinken", "inv_misc_food_64", 1 * G + 70 * S, 110, "consum"),
    (14530, "Schwerer Runenstoffverband", 1, 0, "Verbrauchbares", 7, "Verband", "inv_misc_bandage_12", 1 * G + 30 * S, 250, "consum"),
    (15993, "Thorium-Granate", 1, 0, "Verbrauchbares", 8, "Sonstiges", "inv_misc_bomb_08", 2 * G + 50 * S, 90, "consum"),
    (12404, "Dichter Schleifstein", 1, 0, "Verbrauchbares", 8, "Sonstiges", "inv_stone_sharpeningstone_05", 80 * S, 180, "consum"),
    # Gegenstandsverbesserung / Rezepte
    (11643, "Lebensessenz-Nadel", 2, 8, "Gegenstandsverbesserung", 0, "Sonstiges", "inv_misc_gem_bloodstone_02", 12 * G, 8, "rare"),
    (12811, "Rechtschaffene Kugel", 2, 7, "Handwerkswaren", 11, "Sonstiges", "inv_misc_gem_pearl_03", 32 * G, 15, "rare"),
    (16244, "Formel: Waffe – Kreuzfahrer", 3, 9, "Rezept", 8, "Verzauberkunst", "inv_scroll_03", 240 * G, 2, "recipe"),
    (12720, "Pläne: Thoriumbrustplatte", 2, 9, "Rezept", 4, "Schmiedekunst", "inv_scroll_03", 18 * G, 3, "recipe"),
    (13518, "Rezept: Fläschchen der Macht", 2, 9, "Rezept", 6, "Alchemie", "inv_scroll_03", 9 * G, 3, "recipe"),
    # Ausrüstung
    (12640, "Löwenherzhelm", 4, 4, "Rüstung", 4, "Platte", "inv_helmet_36", 380 * G, 1, "gear"),
    (14551, "Erdrückende Stulpen", 4, 4, "Rüstung", 4, "Platte", "inv_gauntlets_26", 290 * G, 1, "gear"),
    (13340, "Umhang der Feuerkraft", 4, 4, "Rüstung", 1, "Umhang", "inv_misc_cape_18", 210 * G, 1, "gear"),
    (13344, "Gauklerschuhe", 3, 4, "Rüstung", 2, "Leder", "inv_boots_08", 35 * G, 2, "gear"),
    (15062, "Teufelsweberschuhe", 3, 4, "Rüstung", 1, "Stoff", "inv_boots_cloth_05", 28 * G, 2, "gear"),
    (12584, "Großschwert der Arkanitwache", 3, 2, "Waffe", 8, "Zweihandschwert", "inv_sword_36", 42 * G, 2, "gear"),
    (12783, "Herzsucher", 4, 2, "Waffe", 7, "Einhandschwert", "inv_sword_42", 520 * G, 1, "gear"),
    (13041, "Klinge der Wildnis", 2, 2, "Waffe", 7, "Einhandschwert", "inv_sword_04", 6 * G, 3, "gear"),
    (2099, "Zwergische Handkanone", 3, 2, "Waffe", 3, "Schusswaffe", "inv_weapon_rifle_09", 140 * G, 1, "gear"),
    (4614, "Anhänger des Halfdan", 2, 4, "Rüstung", 0, "Schmuck", "inv_jewelry_necklace_03", 4 * G, 3, "gear"),
    # Behälter / Sonstiges / Haustiere
    (14046, "Runenstofftasche", 2, 1, "Behälter", 0, "Tasche", "inv_misc_bag_10_blue", 22 * G, 25, "consum"),
    (4500, "Reiserucksack", 2, 1, "Behälter", 0, "Tasche", "inv_misc_bag_08", 5 * G, 18, "consum"),
    (21841, "Netherstofftasche", 2, 1, "Behälter", 0, "Tasche", "inv_misc_bag_enchantedmageweave", 45 * G, 15, "consum"),
    (8494, "Papageienkäfig (Hyazinth-Ara)", 3, 15, "Verschiedenes", 2, "Haustier", "inv_misc_birdbeck_01", 900 * G, 1, "rare"),
    (10822, "Dunkelwelpling", 3, 15, "Verschiedenes", 2, "Haustier", "inv_misc_head_dragon_black", 160 * G, 1, "rare"),
    (13582, "Zergling-Leine", 3, 15, "Verschiedenes", 2, "Haustier", "ability_hunter_pet_crab", 75 * G, 1, "rare"),
    (21100, "Münze des Glücks", 1, 15, "Verschiedenes", 0, "Plunder", "inv_misc_coin_02", 50 * S, 120, "mat"),
]

HOUSES = [
    ("eu-110", "Allianz-Auktionshaus #110", "eu", "Allianz", 1.00),
    ("eu-111", "Horde-Auktionshaus #111", "eu", "Horde", 1.06),
    ("eu-112", "Schwarzwasser-Auktionshaus #112", "eu", "Neutral", 1.12),
    ("us-210", "Alliance Auction House #210", "us", "Allianz", 0.94),
]

KIND = {
    #        vol/h,  drift,  tagesgang, wochentag, spike-chance
    "mat":         (0.012, 0.0, 0.05, 0.03, 0.0015),
    "consum":      (0.015, 0.0, 0.06, 0.10, 0.0020),
    "consum_rare": (0.020, 0.0, 0.07, 0.14, 0.0020),
    "rare":        (0.025, 0.0, 0.03, 0.03, 0.0030),
    "recipe":      (0.030, 0.0, 0.02, 0.02, 0.0030),
    "gear":        (0.030, 0.0, 0.02, 0.02, 0.0030),
}


def _series(rng: random.Random, hours: int, start_ts: int, kind: str):
    vol, _, amp_d, amp_w, spike = KIND[kind]
    drift = rng.gauss(0, 0.0009)              # Trend pro Stunde (≈ ±2 %/Tag)
    level = 0.0
    out = []
    spike_left, spike_mag = 0, 0.0
    deal = rng.random() < 0.18                 # aktueller Preissturz am Ende
    surge = rng.random() < 0.12                # Preisexplosion in den letzten Tagen
    for h in range(hours):
        ts = start_ts + h * 3600
        lt = time.localtime(ts)
        level += drift + rng.gauss(0, vol) - 0.02 * level  # mean reversion
        daily = amp_d * math.sin((lt.tm_hour - 13) / 24 * 2 * math.pi)  # Spitze ~19 Uhr
        weekly = amp_w if lt.tm_wday in (1, 2) else (-amp_w / 3 if lt.tm_wday in (5, 6) else 0)
        if spike_left == 0 and rng.random() < spike:
            spike_left = rng.randint(1, 4)
            spike_mag = rng.choice([1, -1]) * rng.uniform(0.35, 0.9)
        extra = spike_mag if spike_left else 0.0
        spike_left = max(0, spike_left - 1)
        tail = 0.0
        if deal and h > hours - rng.randint(3, 10):
            tail = -0.35
        if surge and h > hours - 72:
            tail += 0.25 * (h - (hours - 72)) / 72
        out.append((ts, level + daily + weekly + extra + tail, daily))
    return out


def generate(con, days: int = 45, seed: int = 42, end_ts: int | None = None,
             ladder_keep: int = 48, log=print) -> ImportResult:
    rng = random.Random(seed)
    res = ImportResult(SOURCE)
    hours = days * 24
    end_ts = end_ts or int(time.time()) // 3600 * 3600
    start_ts = end_ts - (hours - 1) * 3600

    with db.transaction(con):
        for (iid, name, q, cid, cname, scid, scname, icon, base, vol, kind) in ITEMS:
            db.upsert_item(con, iid, "ok", name=name, quality=q, class_id=cid, class_name=cname,
                           subclass_id=scid, subclass_name=scname, icon=icon,
                           is_commodity=1 if kind in ("mat", "consum", "consum_rare") else 0,
                           vendor_sell=max(1, base // 8))
    house_ids = []
    for ext, name, region, faction, mult in HOUSES:
        with db.transaction(con):
            hid = db.upsert_house(con, SOURCE, ext, name, region=region, faction=faction,
                                  ruleset="1", granularity="hour")
        house_ids.append(hid)
        series = {}
        for (iid, *_rest) in ITEMS:
            base, vol, kind = _rest[7], _rest[8], _rest[9]
            series[iid] = (base * mult * rng.uniform(0.92, 1.08), vol, kind,
                           _series(rng, hours, start_ts, kind))
        with db.transaction(con):
            for h in range(hours):
                ts = start_ts + h * 3600
                listings = []
                for iid, (base, vol, kind, ser) in series.items():
                    _, lvl, daily = ser[h]
                    center = base * math.exp(lvl)
                    # Angebot: abends weniger (wird gekauft), nachts mehr
                    avail = vol * (1 - daily * 2) * rng.uniform(0.7, 1.3)
                    if kind in ("gear", "rare", "recipe"):
                        n_auct = max(0, int(rng.gauss(avail * 2 + 1, 1)))
                    else:
                        n_auct = max(1, int(avail / 8 + rng.uniform(2, 9)))
                    for _ in range(n_auct):
                        price = int(center * math.exp(abs(rng.gauss(0, 0.12)) + rng.gauss(0, 0.02)))
                        if kind in ("gear", "rare", "recipe"):
                            qty = 1
                        else:
                            qty = max(1, int(rng.choice([1, 5, 10, 20, 20, 20, 50]) *
                                             rng.uniform(0.5, 1.2)))
                        listings.append((iid, max(price, 1), qty))
                stats, ladders = aggregate_listings(listings)
                keep_ladder = h >= hours - ladder_keep
                db.write_snapshot(con, hid, ts, SOURCE, stats, ladders if keep_ladder else None)
                res.snapshots += 1
                res.price_points += len(stats)
        log(f"{name}: {hours} Snapshots erzeugt")
    with db.transaction(con):
        db.refresh_summary(con, house_ids)
        db.bump_data_version(con)
        res.houses = len(house_ids)
        res.items = len(ITEMS)
        db.log_import(con, SOURCE, "ok", res.summary())
    return res
