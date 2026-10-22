#!/bin/bash
# Move arm worktrees to the current tips of their branches, then rebuild.
# Usage: update_arms.sh [P=feat/pi-ports] [H=feat/ahe-ports]
set -eu
cd /root/dshbench/arms/R
git fetch -q origin "+refs/heads/*:refs/remotes/origin/*"
declare -A BR=([P]=feat/pi-ports [H]=feat/ahe-ports)
for kv in "$@"; do BR[${kv%%=*}]=${kv#*=}; done
for a in P H; do
  git -C "../$a" checkout -q --detach "origin/${BR[$a]}"
  echo "$a -> ${BR[$a]} $(git -C ../$a rev-parse --short HEAD)"
done
/root/dshbench/build_arms.sh P H
