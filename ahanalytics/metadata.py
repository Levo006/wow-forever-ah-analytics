"""Item-Metadaten nachladen: Wowhead (Forever-Datenbank), optional Battle.net."""
from __future__ import annotations


def fill_metadata(cfg, con, log=print, limit=10000) -> int:
    source = (cfg.get("metadata", {}) or {}).get("source", "auto")
    found = 0
    if source in ("auto", "wowhead"):
        from .sources import wowhead
        try:
            found += wowhead.fetch_item_metadata(con, limit=limit, log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"Wowhead-Metadaten nicht verfügbar: {exc}")
    bn = cfg.get("battlenet", {})
    if source in ("auto", "battlenet") and bn.get("client_id") and bn.get("client_secret"):
        from .sources import battlenet
        try:
            client = battlenet.BattleNetClient(bn["client_id"], bn["client_secret"],
                                               region=(bn.get("regions") or ["eu"])[0],
                                               locale=bn.get("locale", "de_DE"))
            found += battlenet.fetch_item_metadata(con, client, log=log)
        except Exception as exc:  # noqa: BLE001
            log(f"Battle.net-Metadaten nicht verfügbar: {exc}")
    return found
