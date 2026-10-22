#!/bin/bash
# Shallow-fetch each new hard-set repo at one base commit and ingest it into scm_swe.
set -u
S=/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/a1a4e9c7-6159-4d41-aebc-38817012f0e0/scratchpad
SCM="/Users/tazo/Desktop/code mcp/senior-code-mcp"
cd $S/swerun/index
while read -r short url sha; do
  echo "== $short $(date +%T)"
  rm -rf "$short"; mkdir "$short"
  git -C "$short" init -q && git -C "$short" fetch -q --depth 1 "$url" "$sha" && git -C "$short" checkout -q FETCH_HEAD
  t0=$(date +%s)
  QDRANT_COLLECTION=scm_swe GRAPH_PATH="$S/swerun/index/$short.graph.json" "$SCM/.venv/bin/senior-code-ingest" "$S/swerun/index/$short" > "$short.log" 2>&1
  echo "$short rc=$? secs=$(( $(date +%s) - t0 ))"
done <<LIST
xarray https://github.com/pydata/xarray.git 45c0a114e2b7b27b83c9618bc05b36afac82183c
sphinx https://github.com/sphinx-doc/sphinx.git 6cb783c0024a873722952a67ebb9f41771c8eb6d
astropy https://github.com/astropy/astropy.git fa4e8d1cd279acf9b24560813c8652494ccd5922
scikit-learn https://github.com/scikit-learn/scikit-learn.git f9a1cf072da9d7375d6c2163f68a6038b13b310f
LIST
echo DONE
