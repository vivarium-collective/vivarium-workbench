#!/bin/bash
# Reproduces the token/cost profile behind docs/ai-chat.md ("Claude Code"): what a growing chat costs per turn
# when it is served by the real `claude` CLI three ways. Needs `claude` on PATH and signed in, plus `jq`.
#
#   scripts/profile_claude_code_tokens.sh <replay|session|persistent> [turns=4] [model=sonnet]
#
#   replay      fresh process each turn, the whole chat resent as <user>/<assistant> blocks
#   session     fresh process each turn, continuing Claude Code's own saved session (--resume)
#   persistent  ONE long-lived process fed by stream-json, one message at a time (what the workbench does)
#
# Each turn carries ~6000 characters of real text (docs/ai-chat.md, consecutive slices) so the context really
# grows. Numbers are the CLI's own `result.usage` / `total_cost_usd` (API-list-equivalent dollars; `session` and
# `persistent` report a running total, so the per-turn column is the difference). Spends a few cents; no workspace
# or workbench state is touched (empty temp dir, --no-session-persistence except `session`, which is the point).
set -u
MODE="${1:?usage: $0 <replay|session|persistent> [turns] [model]}"; TURNS="${2:-4}"; MODEL="${3:-sonnet}"
DOC="$(cd "$(dirname "$0")/.." && pwd)/docs/ai-chat.md"
SYS='You are a plain chat assistant. If the first message holds earlier turns as <user>/<assistant> blocks, reply as the assistant to the final <user> block.'
W=$(mktemp -d); cd "$W" || exit 1
trap 'cd /; [ -n "${CPID:-}" ] && kill "$CPID" 2>/dev/null; rm -rf "$W"' EXIT
FLAGS=(--output-format stream-json --verbose --tools "" --strict-mcp-config --setting-sources "" --disable-slash-commands --model "$MODEL" --system-prompt "$SYS")
q() { printf 'Passage:\n%s\n\nIn one sentence, what does this passage say?' "$(tail -c +$(( $1 * 6000 + 1 )) "$DOC" | head -c 6000)"; }
row() { jq -r --arg t "$1" 'select(.type=="result") | "\($t)\t\(.usage.input_tokens)\t\(.usage.cache_creation_input_tokens)\t\(.usage.cache_read_input_tokens)\t\(.usage.output_tokens)\t\(.total_cost_usd)"' "$2"; }
printf 'mode=%s model=%s\nturn\tinput\tcache_create\tcache_read\toutput\tusd(running or per-turn, see header)\n' "$MODE" "$MODEL"
case "$MODE" in
  replay)
    HIST=""
    for ((i=0;i<TURNS;i++)); do
      P="${HIST}<user>$(q $i)</user>"
      printf '%s' "$P" | claude -p "${FLAGS[@]}" --no-session-persistence > "t$i.jsonl" 2>/dev/null
      row $((i+1)) "t$i.jsonl"; HIST="${P}<assistant>$(jq -r 'select(.type=="result")|.result' "t$i.jsonl")</assistant>
"
    done;;
  session)
    ID=$(uuidgen | tr 'A-Z' 'a-z')
    for ((i=0;i<TURNS;i++)); do
      if [ $i = 0 ]; then A=(--session-id "$ID"); else A=(--resume "$ID"); fi
      q $i | claude -p "${FLAGS[@]}" "${A[@]}" > "t$i.jsonl" 2>/dev/null; row $((i+1)) "t$i.jsonl"
    done;;
  persistent)
    mkfifo in; : > out.jsonl
    claude -p --input-format stream-json "${FLAGS[@]}" --no-session-persistence < in > out.jsonl 2>/dev/null & CPID=$!
    exec 3> in
    for ((i=0;i<TURNS;i++)); do
      jq -nc --arg t "$(q $i)" '{type:"user",message:{role:"user",content:$t}}' >&3
      for _ in $(seq 1 180); do [ "$(grep -c '"type":"result"' out.jsonl)" -ge $((i+1)) ] && break; sleep 1; done
    done
    exec 3>&-; row x out.jsonl | cut -f2- | nl -w1 -s$'\t';;
  *) echo "unknown mode $MODE" >&2; exit 2;;
esac
