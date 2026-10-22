#!/bin/bash
# Start run_hard.py on the VPS in the background (Mac side).
# Usage: launch.sh <log name> <run_hard.py arguments...>
#   e.g. launch.sh hard1 hard.jsonl R,P,H 1,2 --workers 4
# The OpenRouter key is read by withorkey.sh at run time and sent over ssh stdin. It is kept only in
# the memory of the remote process; it is not written to a file and not put on a command line.
# BENCH_PASS_ENV, BENCH_AGENT_TIMEOUT, BENCH_PATCH, BENCH_MIN_CREDIT and the variables named in
# BENCH_PASS_ENV are copied from this shell to the remote run when they are set.
set -euo pipefail
WITHKEY=${WITHKEY:-/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/a1a4e9c7-6159-4d41-aebc-38817012f0e0/scratchpad/withorkey.sh}
VPS=${VPS:-brainvps}
name=$1; shift
args=$(printf '%q ' "$@")
envs=""
for v in BENCH_PASS_ENV BENCH_AGENT_TIMEOUT BENCH_PATCH BENCH_MIN_CREDIT ${BENCH_PASS_ENV//,/ }; do
  case $v in *KEY*|*TOKEN*|*SECRET*) echo "skip $v: other secrets are not sent" >&2; continue ;; esac
  if [ -n "${!v-}" ]; then envs+="$v=$(printf '%q' "${!v}") "; fi
done
# Only the last command goes to the background; `read` takes the key from ssh stdin first.
"$WITHKEY" bash -c 'printf "%s\n" "$OPENROUTER_API_KEY"' | ssh "$VPS" \
  "cd /root/dshbench || exit 1; read -r K; OPENROUTER_API_KEY=\$K $envs setsid nohup python3 run_hard.py $args > logs/$name.log 2>&1 < /dev/null & echo started $name"
