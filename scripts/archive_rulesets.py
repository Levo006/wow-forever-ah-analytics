"""Bounded, read-only V2 archive of distinct EU ruleset/faction markets.
Separate from V1 mixed-market research. Closed UTC days only; missing stays missing.
"""
import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

START = dt.date(2026, 11, 12)
STOP = dt.date(2026, 12, 14)  # exclusive; includes 28 days after Nov 15 plus warm-up
EXT_IDS = tuple('eu-'+str(n) for n in (111,114,117,110,113,116,119,120,121,123,124,125))
DAY = 86400
DAILY_OFFSET = 43199


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def clone_table(src, dst, name):
    columns = src.execute(f'PRAGMA table_info("{name}")').fetchall()
    if not columns:
        raise ValueError(f'Missing required source table: {name}')
    defs = []
    for _, column, kind, *_ in columns:
        if kind.upper() not in ('INTEGER', 'TEXT', 'REAL', 'BLOB', 'NUMERIC', ''):
            raise ValueError('Unexpected column type')
        defs.append('"' + column.replace('"', '""') + '" ' + kind)
    pk = sorted((r[5], r[1]) for r in columns if r[5])
    if pk:
        defs.append('PRIMARY KEY (' + ','.join('"' + n.replace('"', '""') + '"' for _, n in pk) + ')')
    dst.execute(f'CREATE TABLE "{name}" ({",".join(defs)})')
    return len(columns)


def export(source, out, source_label, now=None, existing=()):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    uri = Path(source).resolve().as_uri() + '?mode=ro'
    with sqlite3.connect(uri, uri=True) as src:
        src.execute('PRAGMA query_only=ON')
        if src.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('Source integrity check failed')
        houses = src.execute("SELECT id,ext_id FROM houses WHERE source='battlenet' AND granularity='hour' AND ext_id IN (?,?,?,?,?,?,?,?,?,?,?,?)", EXT_IDS).fetchall()
        if len(houses) != 12:
            raise ValueError('Expected exactly twelve mapped ruleset/faction markets')
        ids = [r[0] for r in houses]
        marks = ','.join('?' for _ in ids)
        latest = [src.execute('SELECT MAX(ts) FROM snapshots WHERE house_id=? AND ts%86400!=?', (hid, DAILY_OFFSET)).fetchone()[0] for hid in ids]
        if any(t is None for t in latest):
            raise ValueError('Ruleset market without hourly snapshots')
        now = now or dt.datetime.now(dt.timezone.utc)
        stop = min(STOP, now.date())
        lower = int(dt.datetime.combine(START, dt.time(), dt.timezone.utc).timestamp())
        upper = int(dt.datetime.combine(stop, dt.time(), dt.timezone.utc).timestamp())
        days = src.execute(f'SELECT DISTINCT ts/86400 FROM prices WHERE house_id IN ({marks}) AND ts>=? AND ts<? AND ts%86400!=? ORDER BY 1', (*ids, lower, upper, DAILY_OFFSET)).fetchall()
        source_hash = sha256(source) if days else None
        written = []
        for (day,) in days:
            date = dt.datetime.fromtimestamp(day * DAY, dt.timezone.utc).date()
            stem = 'ah-rulesets-v2-' + date.isoformat()
            if stem + '.tar' in existing:
                continue
            packed = out / (stem + '.sqlite.gz')
            manifest_path = out / (stem + '.json')
            if packed.exists() and manifest_path.exists():
                continue
            raw = out / (stem + '.sqlite')
            raw.unlink(missing_ok=True)
            lo, hi = day * DAY, (day + 1) * DAY
            with sqlite3.connect(raw) as dst:
                for name in ('houses', 'prices', 'snapshots', 'market_runs', 'items'):
                    ncols = clone_table(src, dst, name)
                    if name == 'houses':
                        query, params = f'SELECT * FROM houses WHERE id IN ({marks})', ids
                    elif name in ('prices', 'snapshots'):
                        query, params = f'SELECT * FROM {name} WHERE house_id IN ({marks}) AND ts>=? AND ts<? AND ts%86400!=?', (*ids, lo, hi, DAILY_OFFSET)
                    elif name == 'market_runs':
                        query = f'SELECT * FROM market_runs WHERE id IN (SELECT run_id FROM snapshots WHERE house_id IN ({marks}) AND ts>=? AND ts<? AND ts%86400!=?)'
                        params = (*ids, lo, hi, DAILY_OFFSET)
                    else:
                        query = f'SELECT * FROM items WHERE id IN (SELECT DISTINCT item_id FROM prices WHERE house_id IN ({marks}) AND ts>=? AND ts<? AND ts%86400!=?)'
                        params = (*ids, lo, hi, DAILY_OFFSET)
                    dst.executemany(f'INSERT INTO {name} VALUES ({",".join("?" for _ in range(ncols))})', src.execute(query, params))
                dst.commit()
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Export integrity check failed')
                if dst.execute('SELECT COUNT(*) FROM prices p LEFT JOIN snapshots s ON p.house_id=s.house_id AND p.ts=s.ts WHERE s.id IS NULL').fetchone()[0]:
                    raise ValueError('Price observation without snapshot')
                counts = []
                for hid, ext in houses:
                    actual = dst.execute('SELECT COUNT(*),MIN(ts),MAX(ts) FROM prices WHERE house_id=?', (hid,)).fetchone()
                    expected = src.execute('SELECT COUNT(*) FROM prices WHERE house_id=? AND ts>=? AND ts<? AND ts%86400!=?', (hid, lo, hi, DAILY_OFFSET)).fetchone()[0]
                    if actual[0] != expected:
                        raise ValueError('Export row count mismatch')
                    snaps = dst.execute('SELECT COUNT(*),SUM(complete=1),SUM(complete=0),SUM(complete IS NULL) FROM snapshots WHERE house_id=?', (hid,)).fetchone()
                    counts.append(dict(house_id=hid, ext_id=ext, price_rows=actual[0], first_ts=actual[1], last_ts=actual[2], snapshots=snaps[0], complete=snaps[1], partial=snaps[2], unknown_completeness=snaps[3]))
            with open(raw, 'rb') as inp, open(packed, 'wb') as file:
                with gzip.GzipFile(filename='', mode='wb', fileobj=file, mtime=0, compresslevel=6) as gz:
                    shutil.copyfileobj(inp, gz)
            manifest = dict(format='AH-RULESET-HOURLY-V2', day_utc=date.isoformat(), source=source_label, source_sha256=source_hash, created_at=now.isoformat(), sqlite_sha256=sha256(raw), gzip_sha256=sha256(packed), sqlite_bytes=raw.stat().st_size, gzip_bytes=packed.stat().st_size, houses=counts, ruleset_mapping={'normal':[111,114,117],'pvp':[110,113,116],'rp':[119,120,121],'rppvp':[123,124,125]}, caveat='Independent ruleset/faction economies. Never combine with V1 pooled series. Deduplicate by house_id,item_id,ts. Coverage counts are observations, not expected hourly completeness. Observed hourly rows only, not proof of sales. Missing observations are not zero. Use snapshots.complete and market_runs.manifest to filter partial markets. No order-book ladders included.')
            manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
            raw.unlink()
            written.append(manifest)
            print(stem, sum(c['price_rows'] for c in counts), packed.stat().st_size, flush=True)
        return written


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--source-label', required=True)
    p.add_argument('--existing-assets', help='File of already published immutable TAR names')
    args = p.parse_args()
    existing = Path(args.existing_assets).read_text().splitlines() if args.existing_assets else []
    export(args.source, args.out, args.source_label, existing=existing)
