#!/usr/bin/env bash
# KonuLab'ın yerel GPU çalışma ortamını model yüklemeden denetler.
set -euo pipefail

MODEL_NAME="${OLLAMA_MODEL:-qwen3.8:27b}"
failed=0

ok() { printf '✓ %s\n' "$1"; }
warn() { printf '! %s\n' "$1" >&2; failed=1; }

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_info="$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -n 1)"
  ok "GPU: ${gpu_info}"
else
  warn "nvidia-smi bulunamadı; NVIDIA sürücüsü görünmüyor."
fi

if docker info --format '{{json .Runtimes.nvidia}}' 2>/dev/null | grep -q 'nvidia'; then
  ok "Docker NVIDIA runtime kullanılabilir"
else
  warn "Docker NVIDIA runtime görünmüyor. NVIDIA Container Toolkit'i kontrol edin."
fi

if curl --fail --silent --show-error http://127.0.0.1:6333/healthz >/dev/null; then
  ok "Qdrant erişilebilir"
else
  warn "Qdrant erişilemiyor; docker compose -f compose.yaml up -d qdrant komutunu çalıştırın."
fi

if curl --fail --silent --show-error http://127.0.0.1:11434/api/tags >/dev/null; then
  ok "Ollama servisi erişilebilir"
else
  warn "Ollama erişilemiyor; sudo systemctl start ollama komutunu çalıştırın."
fi

if command -v ollama >/dev/null 2>&1 && ollama list 2>/dev/null | awk -v model="$MODEL_NAME" '$1 == model { found = 1 } END { exit !found }'; then
  ok "Ollama modeli kurulu: ${MODEL_NAME}"
else
  warn "Ollama modeli henüz kurulu değil: ${MODEL_NAME}"
fi

disk_line="$(df -h . | awk 'NR == 2 { print $4 " boş / " $2 " toplam" }')"
ok "Proje diski: ${disk_line}"

if [ "$failed" -ne 0 ]; then
  exit 1
fi
