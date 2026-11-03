#!/usr/bin/env bash
# modelrouter-ai smoke test: unit + e2e suites, CLI surface, live proxy
# against a fake backend. No network beyond localhost.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== compile check =="
python3 -m compileall -q modelrouter tests

echo "== unit + e2e tests =="
python3 -m unittest discover -s tests

echo "== CLI surface =="
python3 -m modelrouter --help > /dev/null
python3 -m modelrouter --config config.example.yaml route \
  --explain "write a function that adds two numbers" --tier cascade > /dev/null
python3 -m modelrouter --config config.example.yaml --data-dir /tmp/mr-smoke-data \
  costs > /dev/null
python3 -m modelrouter --config config.example.yaml --data-dir /tmp/mr-smoke-data \
  costs --html > /dev/null
rm -rf /tmp/mr-smoke-data

echo "SMOKE OK"
