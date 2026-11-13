"""Kommandozeile: ``python -m ahanalytics <befehl>``"""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import config, db


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="ahanalytics",
                                 description="WoW: Forever – Auktionshaus-Analytics")
    ap.add_argument("--config", help="Pfad zur config.toml")
    ap.add_argument("--db", help="Pfad zur SQLite-Datenbank (überschreibt config)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Website starten")
    s.add_argument("--port", type=int)
    s.add_argument("--host")
    s.add_argument("--collect", action="store_true",
                   help="Battle.net-Sammler im Hintergrund mitlaufen lassen")

    d = sub.add_parser("demo", help="Beispieldaten erzeugen")
    d.add_argument("--days", type=int, default=45)
    d.add_argument("--seed", type=int, default=42)

    a = sub.add_parser("import-auctionator", help="Auctionator.lua importieren")
    a.add_argument("paths", nargs="*", help="Pfad(e) zur Auctionator.lua (leer = automatisch suchen)")
    a.add_argument("--region", default=None)

    c = sub.add_parser("collect", help="Snapshot über die Battle.net API holen")
    c.add_argument("--loop", action="store_true", help="dauerhaft im Intervall sammeln")
    c.add_argument("--no-metadata", action="store_true")

    e = sub.add_parser("export", help="Statische Website (z. B. für GitHub Pages) erzeugen")
    e.add_argument("--out", default=None, help="Zielordner (Standard: docs/)")
    e.add_argument("--history-days", type=int, default=180)
    e.add_argument("--snapshot-files", type=int, default=6,
                   help="Anzahl jüngster Snapshots je Haus mit Detaildatei")
    e.add_argument("--repo-url", default=None, help="Link zum Code (wird auf der Seite angezeigt)")

    m = sub.add_parser("metadata", help="Item-Namen, Qualität, Kategorie und Icons nachladen")
    m.add_argument("--english-only", action="store_true", help="nur englische Namen nachladen")
    m.add_argument("--limit", type=int, default=10000, help="höchstens so viele Items pro Lauf (Wowhead)")
    m.add_argument("--until-done", action="store_true",
                   help="bei Drosselung durch Wowhead 20 Minuten warten und weitermachen, bis alles geladen ist")

    pb = sub.add_parser("publish", help="Website exportieren und auf GitHub Pages (Zweig gh-pages) veröffentlichen")
    pb.add_argument("--no-push", action="store_true", help="nur nach site/ exportieren, nicht hochladen")

    cp = sub.add_parser("compact", help="alte Stundenwerte zu Tageswerten verdichten (Dauerbetrieb)")
    cp.add_argument("--hourly-days", type=int, default=7)
    cp.add_argument("--keep-days", type=int, default=180)
    cp.add_argument("--house-hourly-days", type=int, default=2, help="Einzelhäuser: Tage stündlich")
    cp.add_argument("--house-keep-days", type=int, default=14, help="Einzelhäuser: Tage insgesamt")
    cp.add_argument("--vacuum", action="store_true", help="Datei danach verkleinern (nur wenn etwas gelöscht wurde)")

    rc = sub.add_parser("recipes", help="Berufe und Rezepte von Battle.net laden (nur wenn veraltet)")
    rc.add_argument("--force", action="store_true", help="auch laden, wenn der Bestand aktuell ist")
    rc.add_argument("--max-age-days", type=int, default=7)

    sub.add_parser("refresh", help="Zusammenfassungen neu berechnen")
    sub.add_parser("info", help="Datenbank-Statistik anzeigen")

    args = ap.parse_args(argv)
    cfg = config.load(args.config)
    if args.db:
        cfg["server"]["db_path"] = args.db
    path = cfg["server"]["db_path"]

    if args.cmd == "serve":
        if args.port:
            cfg["server"]["port"] = args.port
        if args.host:
            cfg["server"]["host"] = args.host
        from .server import serve
        serve(cfg, with_collector=args.collect)
        return 0

    con = db.connect(path)
    con.isolation_level = None  # Transaktionen explizit über db.transaction

    if args.cmd == "demo":
        from .sources import demo
        t = time.time()
        res = demo.generate(con, days=args.days, seed=args.seed)
        print(res.summary(), f"({time.time() - t:.1f} s)")
    elif args.cmd == "import-auctionator":
        from .sources import auctionator
        av = cfg["auctionator"]
        paths = args.paths or ([av["path"]] if av.get("path")
                               else auctionator.find_savedvariables(av["wow_root"]))
        if not paths:
            print("Keine Auctionator.lua gefunden. Pfad angeben oder in config.toml setzen.")
            return 1
        for p in paths:
            res = auctionator.import_file(con, p, region=args.region or av.get("region", "eu"))
            print(f"{p}\n  {res.summary()}")
        _maybe_metadata(cfg, con, limit=300)  # max. ~5 Minuten; Rest beim nächsten Lauf
    elif args.cmd == "collect":
        from .sources import battlenet
        bn = cfg["battlenet"]
        interval = max(5, int(cfg["collector"]["interval_minutes"])) * 60
        while True:
            for region in bn.get("regions") or ["eu"]:
                try:
                    client = battlenet.BattleNetClient(bn["client_id"], bn["client_secret"],
                                                       region=region, locale=bn.get("locale", "de_DE"))
                    res = battlenet.collect(con, client, ruleset=bn.get("ruleset") or None,
                                            only_houses=bn.get("houses") or None,
                                            ladder_keep=cfg["collector"]["ladder_keep"],
                                            fetch_metadata=not args.no_metadata)
                    print(time.strftime("%Y-%m-%d %H:%M"), res.summary())
                except Exception as exc:  # noqa: BLE001
                    print(time.strftime("%Y-%m-%d %H:%M"), f"Fehler ({region}): {exc}")
                    with db.transaction(con):
                        db.log_import(con, "battlenet", "error", str(exc))
                    if not args.loop:
                        return 1
            if not args.loop:
                break
            time.sleep(interval)
    elif args.cmd == "export":
        from . import export
        out = args.out or os.path.join(config.ROOT, "docs")
        export.export_site(con, out, history_days=args.history_days,
                           snapshot_files=args.snapshot_files,
                           repo_url=args.repo_url or cfg.get("publish", {}).get("repo_url", ""))
    elif args.cmd == "metadata":
        from .sources import wowhead

        def remaining():
            cutoff = int(time.time()) - 7 * 86400
            de = 0 if args.english_only else con.execute(
                "SELECT COUNT(*) FROM items WHERE meta_status='pending'").fetchone()[0]
            en = con.execute("""SELECT COUNT(*) FROM items WHERE name_en IS NULL AND meta_status != 'missing'
                                AND COALESCE(en_checked,0) < ?""", (cutoff,)).fetchone()[0]
            return de, en

        while True:
            before = remaining()
            if args.english_only:
                wowhead.fetch_english_names(con, limit=args.limit)
            else:
                _maybe_metadata(cfg, con, limit=args.limit)
            after = remaining()
            print(time.strftime("%H:%M"), f"offen: {after[0]} deutsche, {after[1]} englische Namen", flush=True)
            if not args.until_done or after == (0, 0):
                break
            if after == before:
                print("Keine Fortschritte (Wowhead drosselt) – warte 20 Minuten …", flush=True)
            else:
                print("Wowhead drosselt – warte 20 Minuten …", flush=True)
            time.sleep(20 * 60)
    elif args.cmd == "publish":
        from .publish import publish
        publish(con, cfg, push=not args.no_push)
    elif args.cmd == "compact":
        st = db.compact(con, hourly_days=args.hourly_days, keep_days=args.keep_days,
                        house_hourly_days=args.house_hourly_days, house_keep_days=args.house_keep_days)
        with db.transaction(con):
            db.prune_ladders(con, cfg["collector"]["ladder_keep"])
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")  # alles in die Hauptdatei (wird danach gepackt)
        if args.vacuum and (st["days"] or st["deleted"]):
            con.execute("VACUUM")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    elif args.cmd == "recipes":
        from . import recipes
        from .sources import battlenet
        bn = cfg["battlenet"]
        client = battlenet.BattleNetClient(bn["client_id"], bn["client_secret"],
                                           region=(bn.get("regions") or ["eu"])[0], locale=bn.get("locale", "de_DE"))
        recipes.update(con, client, force=args.force, max_age_days=args.max_age_days)
    elif args.cmd == "refresh":
        with db.transaction(con):
            db.refresh_summary(con)
            db.bump_data_version(con)
        print("Zusammenfassungen aktualisiert.")
    elif args.cmd == "info":
        for r in con.execute(
                """SELECT h.id, h.source, h.region, h.name, h.granularity, COUNT(s.id) n,
                          datetime(MIN(s.ts),'unixepoch','localtime') von,
                          datetime(MAX(s.ts),'unixepoch','localtime') bis
                   FROM houses h LEFT JOIN snapshots s ON s.house_id=h.id GROUP BY h.id"""):
            print(dict(r))
        print("Items:", con.execute("SELECT COUNT(*) FROM items").fetchone()[0],
              "| Preispunkte:", con.execute("SELECT COUNT(*) FROM prices").fetchone()[0])
    return 0


def _maybe_metadata(cfg, con, limit=10000):
    from .metadata import fill_metadata
    fill_metadata(cfg, con, limit=limit)


if __name__ == "__main__":
    sys.exit(main())
