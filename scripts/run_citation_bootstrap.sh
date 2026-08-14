#!/usr/bin/env bash
set -uo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

batch_size=${TITLE_BATCH_SIZE:-10000}
request_interval=${S2_REQUEST_INTERVAL:-1.05}
cooldown_seconds=${S2_COOLDOWN_SECONDS:-60}
export PYTHONUNBUFFERED=1

pending_count() {
  PYTHONPATH=scripts python - <<'PY'
import datetime as dt
import json
from pathlib import Path

import update_citation_leaderboard as leaderboard

notes = leaderboard.scan_notes(Path("docs"))
cache = json.loads(Path("data/semantic-scholar-cache.json").read_text())
today = dt.datetime.now(dt.timezone.utc).date()
print(
    sum(
        leaderboard.should_search_title(
            note,
            cache["matches"].get(note.path),
            today=today,
            retry_after_days=30,
        )
        for note in notes
    )
)
PY
}

while true; do
  before=$(pending_count)
  printf '[%s] pending title lookups: %s\n' "$(date -u +%FT%TZ)" "$before"
  if [ "$before" -eq 0 ]; then
    printf '[%s] Semantic Scholar title bootstrap complete.\n' "$(date -u +%FT%TZ)"
    exit 0
  fi

  python scripts/update_citation_leaderboard.py \
    --skip-refresh \
    --max-title-requests "$batch_size" \
    --request-interval "$request_interval"
  status=$?
  after=$(pending_count)
  printf '[%s] pass finished: %s -> %s pending (status %s)\n' \
    "$(date -u +%FT%TZ)" "$before" "$after" "$status"

  if [ "$after" -eq 0 ]; then
    continue
  fi
  if [ "$status" -ne 0 ] || [ "$after" -ge "$before" ]; then
    printf '[%s] no safe progress; cooling down for 300 seconds\n' "$(date -u +%FT%TZ)"
    sleep 300
  else
    printf '[%s] cooling down for %s seconds\n' \
      "$(date -u +%FT%TZ)" "$cooldown_seconds"
    sleep "$cooldown_seconds"
  fi
done
