# Upstream Place IL Quest Material

## Source

Official repository:

`https://github.com/liraz-place-il/Quests`

Relevant directory:

`Quest 4/Stage 1`

Pinned Stage 1 source commit used by this submission:

`250a2e9e43189f8cf3e19260f633ed636996d68c`

The current upstream `main` is one commit ahead (`6f8efa381de7f0a18b947534ae6a1a762f2d3391`). A commit comparison shows that the added commit introduces Stage 2 material plus top-level README references; **no Stage 1 file changed**. The pin therefore still matches the current Stage 1 source.

## IP constraint

The upstream repository states that Place IL owns the challenge briefs, starter kits, source code, fixtures, and documentation and that they may not be copied/reproduced/distributed without prior written permission.

This repository therefore does **not** vendor or republish those files. It uses a read-only local checkout for development/verification.

## Local checkout

Run:

```bash
bash scripts/bootstrap_upstream.sh
```

The script creates an ignored checkout at:

```text
.vendor/place-il-quests
```

The Stage 1 starter kit is then available at:

```text
.vendor/place-il-quests/Quest 4/Stage 1/starter-kit
```

This local checkout must never be committed.

## Supplied verification

The bootstrap script automatically runs the supplied verifier. It can also be run directly:

```bash
python ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
```

Expected result: **33/33 checks passed**.

## Runtime integration

The application imports the supplied `mock_services.py` without modifying it and consumes its `TOOL_SCHEMAS` / `TOOL_REGISTRY` through `app/tools_adapter.py`.

The starter-kit location stays configurable through `QUEST4_STARTER_KIT_PATH` so evaluators/developers can point the project at an authorized local copy.

Business rules are not silently duplicated in this repository.
