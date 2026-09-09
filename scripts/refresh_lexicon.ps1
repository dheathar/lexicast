# Weekly personal-lexicon refresh (scheduled task "Lexicast lexicon refresh",
# Mondays 03:15). Idempotent by design (MAX-frequency upserts): re-running is
# always safe and cheap -- only genuinely NEW surfaces get phonemized.
# Cadence rationale: vocabulary churns weekly at most; more frequent imports
# just farm unreviewed candidates (see CONSTITUTION review-pass note).
# StartWhenAvailable is set on the task: cronos sleeps at night, so the
# Monday 03:15 run fires after wake instead of being skipped.
docker info *> $null
if ($LASTEXITCODE -ne 0) { exit 0 }   # Docker not running = skip, catch up later
$banks = "panos-projects,panos-world-bank,panos-dmlab,panos-ece-papel,panos-notes,harness_dev,panos-homelab"
docker exec lexicast-lexicast-1 python lexicon_import.py --tbwiki /tb_wiki *> $null
docker exec lexicast-lexicast-1 python lexicon_import.py --banks $banks *> $null
docker exec lexicast-lexicast-1 python lexicon.py dump-review *> $null
exit 0
