#!/usr/bin/env bash
# scripts/smoke.sh RUN_ID GPU -- smoke checks S1-S5 (PLAN 4.5 step 5).
# Start the server first with scripts/serve.sh RUN_ID GPU PORT -- ... (Qwen3-0.6B, hub id or local dir);
# this script reads the port from $RUNS/RUN_ID/port, runs S1-S3 against it, stops the
# server through stop_server.sh (S4, asks for confirmation), then runs the existing tests on
# the same GPU through run_offline.sh (S5). Results go to $RUNS/RUN_ID/smoke.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/check_env.sh
# shellcheck source=scripts/env.sh
source scripts/env.sh

[ $# -eq 2 ] || { echo "usage: $0 RUN_ID GPU" >&2; exit 2; }
RUN_ID=$1 GPU=$2
[[ "$RUN_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "bad RUN_ID" >&2; exit 2; }
RUN_DIR=$RUNS/$RUN_ID
if [ "${DRY_RUN:-0}" = 1 ]; then
  echo "DRY-RUN: would run S1-S3 against the port in $RUN_DIR/port, then"
  echo "DRY-RUN: scripts/stop_server.sh $RUN_ID (S4) and scripts/run_offline.sh tests-<time> $GPU -- pytest ... (S5)"
  exit 0
fi
[ -s "$RUN_DIR/port" ] || { echo "no $RUN_DIR/port: start the server with scripts/serve.sh first" >&2; exit 1; }
PORT=$(cat "$RUN_DIR/port")
# the --model-path the server was started with (a hub id or a local directory under /home/xwc/vllm/models)
MODEL=$(grep '^command:' "$RUN_DIR/run_meta.txt" | sed -n 's/.* --model-path \([^ ]*\).*/\1/p')
[ -n "$MODEL" ] || { echo "cannot read --model-path from $RUN_DIR/run_meta.txt" >&2; exit 1; }
OUT=$RUN_DIR/smoke.txt
[ ! -e "$OUT" ] || { echo "$OUT exists; smoke already ran for this run" >&2; exit 1; }
URL=http://127.0.0.1:$PORT
pass=1
result() { echo "$1 $2" | tee -a "$OUT"; [ "$2" = PASS ] || pass=0; }

# S1: model listed
if curl -sf "$URL/v1/models" | tee "$RUN_DIR/s1.json" | grep -q "$MODEL"; then result S1 PASS; else result S1 FAIL; fi

# S2: non-streaming request returns text
BODY='{"model":"'$MODEL'","messages":[{"role":"user","content":"Count to five."}],"max_tokens":16,"temperature":0}'
if curl -sf "$URL/v1/chat/completions" -H 'Content-Type: application/json' -d "$BODY" > "$RUN_DIR/s2.json" \
  && python -c 'import json,sys; c=json.load(open(sys.argv[1]))["choices"][0]["message"]["content"]; sys.exit(0 if c.strip() else 1)' "$RUN_DIR/s2.json"; then
  result S2 PASS
else
  result S2 FAIL
fi

# S3: streaming request ends with [DONE]
BODY='{"model":"'$MODEL'","messages":[{"role":"user","content":"Count to five."}],"max_tokens":16,"temperature":0,"stream":true}'
if curl -sfN "$URL/v1/chat/completions" -H 'Content-Type: application/json' -d "$BODY" > "$RUN_DIR/s3.txt" \
  && grep -v '^$' "$RUN_DIR/s3.txt" | tail -n 1 | grep -qx 'data: \[DONE\]'; then
  result S3 PASS
else
  result S3 FAIL
fi

# S4: stop the server; the group must be gone and the GPU memory released
PGID=$(cat "$RUN_DIR/server.pgid")
scripts/stop_server.sh "$RUN_ID" || true
if [ -z "$(ps -o pid= -g "$PGID" 2>/dev/null)" ]; then result S4 PASS; else result S4 FAIL; fi
echo "S4: check that GPU $GPU memory in $RUN_DIR/post_snapshot.txt is back to its pre-run value" | tee -a "$OUT"

# S5: existing CPU/GPU unit tests on the same GPU (skip test_scheduler.py and test_comm.py)
TESTS_ID=tests-$(date +%Y%m%d-%H%M%S)
if scripts/run_offline.sh "$TESTS_ID" "$GPU" -- python -m pytest \
    tests/core/test_cache_allocate.py tests/kernel/test_index.py tests/kernel/test_store.py \
    tests/kernel/test_tensor.py tests/misc -q; then
  result S5 PASS
else
  result S5 FAIL
fi
echo "S5 log: $RUNS/$TESTS_ID/run-offline.log" | tee -a "$OUT"
[ "$pass" = 1 ] && echo "smoke: all PASS" | tee -a "$OUT"
[ "$pass" = 1 ]
