#!/usr/bin/env bash
# Called after collection, before compaction. Failure stops compaction, not silent loss.
set -euo pipefail
[[ "$(date -u +%F)" < "2026-12-15" ]] || exit 0
tag=ah-rulesets-hourly-v2-2026-11
mkdir -p ruleset-archive
if ! gh release view "$tag" --json tagName > /tmp/ruleset-release.json; then
  gh release create "$tag" --target "$GITHUB_SHA" --prerelease --title 'Regelsatz-Stundenarchiv V2 (bis 13.12.2026)' --notes 'AH-RULESET-HOURLY-V2: Getrennte EU-Regelsatz-/Fraktionsmaerkte. Keine Fortsetzung oder Validierung der V1-Mischserie. Beobachtungen, keine Verkaeufe; Luecken bleiben Luecken. Unveraenderliche geschlossene UTC-Tage, 12.11.-13.12.2026. Retention der Produktion unveraendert.'
fi
gh release view "$tag" --json assets --jq '.assets[].name' > /tmp/ruleset-existing.txt
python scripts/archive_rulesets.py --source data/ah.sqlite --out ruleset-archive --source-label "production-precompact:$GITHUB_RUN_ID" --existing-assets /tmp/ruleset-existing.txt
shopt -s nullglob
for manifest in ruleset-archive/*.json; do
  stem="${manifest%.json}"
  tar -cf "$stem.tar" -C ruleset-archive "$(basename "$stem").json" "$(basename "$stem").sqlite.gz"
  gh release upload "$tag" "$stem.tar"
done
