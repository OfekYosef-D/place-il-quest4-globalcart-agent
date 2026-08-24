#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$ROOT_DIR/.vendor/place-il-quests-stage2"
UPSTREAM_URL="https://github.com/liraz-place-il/Quests.git"
UPSTREAM_COMMIT="6f8efa381de7f0a18b947534ae6a1a762f2d3391"

if [ -d "$VENDOR_DIR/.git" ]; then
  echo "Stage 2 upstream checkout already exists at $VENDOR_DIR"
else
  mkdir -p "$(dirname "$VENDOR_DIR")"
  git clone --filter=blob:none --no-checkout "$UPSTREAM_URL" "$VENDOR_DIR"
fi

git -C "$VENDOR_DIR" sparse-checkout init --cone
git -C "$VENDOR_DIR" sparse-checkout set "Quest 4/Stage 2"
git -C "$VENDOR_DIR" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$VENDOR_DIR" checkout --detach "$UPSTREAM_COMMIT"

echo "Pinned Place IL Stage 2 checkout ready."
echo "Starter kit: $VENDOR_DIR/Quest 4/Stage 2/starter-kit"
python3 "$VENDOR_DIR/Quest 4/Stage 2/starter-kit/examples/verify_scenarios.py"
