#!/usr/bin/env bash
# macOS/Linux: downloads and serves the local LLM with LM Studio's CLI.
# Prerequisite: install LM Studio from https://lmstudio.ai and open it once.
set -euo pipefail
MODEL="qwen/qwen3-8b"
ID="qwen3-8b"

if ! command -v lms >/dev/null 2>&1; then
  echo "'lms' CLI not found. Trying to bootstrap it..."
  if [ -x "$HOME/.lmstudio/bin/lms" ]; then "$HOME/.lmstudio/bin/lms" bootstrap; else npx lmstudio install-cli; fi
  echo "Open a NEW terminal and run this script again."; exit 1
fi

echo "Downloading $MODEL (about 5 GB, pick the Q4_K_M option if asked)..."
lms get "$MODEL"
echo "Loading model..."
lms unload --all || true
lms load "$MODEL" --identifier "$ID" --context-length 8192 --gpu max
echo "Starting API server on port 1234..."
lms server start --port 1234
lms ps
echo "Done. LM Studio is serving '$ID' at http://localhost:1234/v1"
