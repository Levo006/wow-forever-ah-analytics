"""Geprüfte Suchaliase, keine Übernahme von Classic-Spielwerten.

Namensbelege: https://guildloot.de/item/era/7753-blutvergiesser/
https://guildloot.de/item/era/8226-der-schlaechter/
Ein Alias behauptet keine bestätigte aktuelle Forever-Lokalisierung.
"""
import json

ALIASES = {7753: ["Blutvergießer"], 8226: ["Der Schlächter"]}


def enrich(row):
    row = dict(row)
    iid = row.get("item_id", row.get("id"))
    row["aliases"] = list(ALIASES.get(iid, []))
    for alias in json.loads(row.get("catalog_aliases") or "[]"):
        if alias and alias not in (row.get("name"), row.get("name_en")) and alias not in row["aliases"]:
            row["aliases"].append(alias)
    row.pop("catalog_aliases", None)
    name = row.get("name")
    row["translation_missing"] = not name or (name.startswith("[") and name.endswith("]"))
    if row["translation_missing"]:
        row["name"] = row.get("name_en") or (name.strip("[]") if name else None) or f"Item #{iid}"
    return row
