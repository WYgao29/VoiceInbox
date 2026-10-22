#!/bin/bash
# 先跑不加载模型的测试，再跑一次真实 Qwen3-ASR。
# 端到端峰值内存大约 9.6GB，不要同时再开别的转写。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
echo "单元测试（不加载 Qwen）"
"$ROOT/.venv/bin/python" -m pytest -m "not e2e" -q
echo "端到端：真实 Qwen3-ASR"
"$ROOT/.venv/bin/python" -m pytest -m e2e -q
