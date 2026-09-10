#!/bin/sh
# Run sequentially so models do not compete for inference memory.
set -eu
cd "$(dirname "$0")/.."
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
for size in 0.5b 1.5b 3b; do
  manifest="artifacts/verification/qwen-$size.json"
  if [ ! -f "$manifest" ]; then
    echo "Missing verified model manifest: $manifest" >&2
    exit 1
  fi
  if [ ! -f "artifacts/evaluation/iteration2-$size.json" ]; then
    .venv/bin/python scripts/benchmark_verified.py "$manifest" --output "artifacts/evaluation/iteration2-$size.json" --device cpu
  fi
  if [ ! -f "artifacts/evaluation/conversation-$size.json" ]; then
    .venv/bin/python scripts/conversation_assessment.py "$manifest" --output "artifacts/evaluation/conversation-$size.json"
  fi
done
