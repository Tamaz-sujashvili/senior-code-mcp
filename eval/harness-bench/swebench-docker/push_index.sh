#!/bin/bash
# Snapshot local Qdrant collection scm_swe, restore it on the VPS as scm_hard, copy graph files.
set -euo pipefail
S=/private/tmp/claude-501/-Users-tazo-Desktop-code-mcp/a1a4e9c7-6159-4d41-aebc-38817012f0e0/scratchpad
cd $S/swerun/index
name=$(rtk proxy curl -s -X POST "localhost:6333/collections/scm_swe/snapshots?wait=true" | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['name'])")
rtk proxy curl -s -o $S/scm_swe.snapshot "localhost:6333/collections/scm_swe/snapshots/$name"
ls -la $S/scm_swe.snapshot
scp -q $S/scm_swe.snapshot brainvps:/root/dshbench/index/scm_swe.snapshot
scp -q django.graph.json sympy.graph.json pytest.graph.json pylint.graph.json xarray.graph.json sphinx.graph.json astropy.graph.json scikit-learn.graph.json brainvps:/root/dshbench/index/
ssh -n brainvps 'cd /root/dshbench/index && curl -s -X DELETE localhost:6333/collections/scm_hard >/dev/null; curl -s -X POST "localhost:6333/collections/scm_hard/snapshots/upload?priority=snapshot&wait=true" -H "Content-Type: multipart/form-data" -F "snapshot=@scm_swe.snapshot" && echo && curl -s localhost:6333/collections/scm_hard | python3 -c "import json,sys; print(\"scm_hard points\", json.load(sys.stdin)[\"result\"][\"points_count\"])" && rm -f scm_swe.snapshot'
rtk proxy curl -s -X DELETE "localhost:6333/collections/scm_swe/snapshots/$name" >/dev/null; rm -f $S/scm_swe.snapshot
