"""Statische Item-Metadaten: Qualitäten, Klassen (deutsch)."""

QUALITY_NAMES = {
    0: "Schlecht", 1: "Gewöhnlich", 2: "Ungewöhnlich", 3: "Selten",
    4: "Episch", 5: "Legendär", 6: "Artefakt", 7: "Erbstück",
}
QUALITY_TYPES = {
    "POOR": 0, "COMMON": 1, "UNCOMMON": 2, "RARE": 3, "EPIC": 4,
    "LEGENDARY": 5, "ARTIFACT": 6, "HEIRLOOM": 7, "WOW_TOKEN": 1,
}

# Item-Klassen (Enum.ItemClass); Namen deutsch wie im Client
CLASS_NAMES = {
    0: "Verbrauchbar", 1: "Behälter", 2: "Waffen", 3: "Edelsteine", 4: "Rüstung",
    5: "Reagenzien", 6: "Projektile", 7: "Handwerkswaren", 8: "Gegenstandsverbesserung",
    9: "Rezepte", 11: "Köcher", 12: "Quest", 13: "Schlüssel", 15: "Verschiedenes",
    16: "Glyphen", 17: "Kampfhaustiere", 18: "WoW-Marke", 19: "Berufe",
}


def class_name(class_id, fallback=None):
    if class_id is None:
        return fallback or "Unbekannt"
    return fallback or CLASS_NAMES.get(int(class_id), f"Klasse {class_id}")


def faction_from_name(name: str) -> str:
    n = (name or "").lower()
    if "allian" in n:
        return "Allianz"
    if "horde" in n:
        return "Horde"
    if "black" in n or "schwarz" in n or "neutral" in n or "goblin" in n:
        return "Neutral"
    return ""
