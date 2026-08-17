#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_DIR="$ROOT_DIR/.vendor/place-il-quests"
UPSTREAM_URL="https://github.com/liraz-place-il/Quests.git"
UPSTREAM_COMMIT="250a2e9e43189f8cf3e19260f633ed636996d68c"

if [ -d "$VENDOR_DIR/.git" ]; then
  echo "Upstream checkout already exists at $VENDOR_DIR"
else
  mkdir -p "$(dirname "$VENDOR_DIR")"
  git clone --filter=blob:none --no-checkout "$UPSTREAM_URL" "$VENDOR_DIR"
fi

git -C "$VENDOR_DIR" sparse-checkout init --cone
git -C "$VENDOR_DIR" sparse-checkout set "Quest 4/Stage 1"
git -C "$VENDOR_DIR" fetch --depth 1 origin "$UPSTREAM_COMMIT"
git -C "$VENDOR_DIR" checkout --detach "$UPSTREAM_COMMIT"

echo "Pinned Place IL Quest checkout ready."
echo "Starter kit: $VENDOR_DIR/Quest 4/Stage 1/starter-kit"
python3 "$VENDOR_DIR/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
