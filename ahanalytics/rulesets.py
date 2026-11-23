"""Verified EU auction-house mapping. Recheck dormant IDs when Hardcore launches."""
RULESETS = {
    "normal": ("Normal", "Normal", (111, 114, 117)),
    "pvp": ("PvP", "PvP", (110, 113, 116)),
    "rp": ("Rollenspiel", "Roleplay", (119, 120, 121)),
    "rppvp": ("Rollenspiel-PvP", "Roleplay-PvP", (123, 124, 125)),
}
INACTIVE_EU = {112, 115, 118}  # Expected inactive, Hardcore assignment is unconfirmed.
FACTIONS = ("Allianz", "Horde", "Neutral")

def identity(ext_id):
    if not str(ext_id).startswith("eu-"):
        return None
    for key, (de, en, ids) in RULESETS.items():
        for faction, num in zip(FACTIONS, ids):
            if ext_id == f"eu-{num}":
                return key, de, en, faction
    return None

def normal_house(con, faction="Allianz"):
    num = RULESETS["normal"][2][FACTIONS.index(faction)]
    row = con.execute("SELECT id FROM houses WHERE source='battlenet' AND ext_id=?", (f"eu-{num}",)).fetchone()
    return row[0] if row else None
