#!/bin/bash
# Install and build harness arms on the VPS. Usage: build_arms.sh R P H
set -u
cd /root/dshbench/arms
for x in "$@"; do
  echo "== $x $(date +%T)"
  (cd "$x" && COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm install --frozen-lockfile > "../../logs/install_$x.log" 2>&1; echo "install rc=$?"
   pnpm run build:artifacts > "../../logs/build_$x.log" 2>&1; echo "build rc=$?")
done
echo "DONE $(date +%T)"
