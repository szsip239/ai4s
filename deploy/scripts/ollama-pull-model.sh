#!/usr/bin/env bash
# 语义层本地 judge 模型拉取（issue #24，幂等）。
# 用法：cd deploy && ./scripts/ollama-pull-model.sh [模型名，默认 qwen2.5:1.5b]
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-qwen2.5:1.5b}"
docker compose up -d ollama
echo "==> 拉取 $MODEL（首次约 1GB）"
docker exec ai4s-ollama ollama pull "$MODEL"
echo "==> 已就位："
docker exec ai4s-ollama ollama list | grep -i "${MODEL%%:*}" || true
echo "切换 judge：.env 设 JUDGE_BASE_URL=http://ollama:11434/v1 JUDGE_API_KEY=ollama JUDGE_MODEL=$MODEL，docker compose up -d --force-recreate shim"
