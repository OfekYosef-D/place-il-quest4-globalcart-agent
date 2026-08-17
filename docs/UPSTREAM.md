# Upstream Place IL Quest Material

## Source

Official repository:

`https://github.com/liraz-place-il/Quests`

Relevant directory:

`Quest 4/Stage 1`

Reference commit used while preparing this handoff:

`250a2e9e43189f8cf3e19260f633ed636996d68c`

## Important IP constraint

The upstream repository states that Place IL owns the challenge briefs, starter kits, source code, fixtures, and documentation and that they may not be copied/reproduced/distributed without prior written permission.

Therefore this project handoff intentionally does **not** vendor or republish those files.

Use the supplied materials as a read-only local dependency/reference. If the participant has separate written permission to copy them into the submission repository, that permission governs instead.

## Local development checkout

Run:

```bash
./scripts/bootstrap_upstream.sh
```

The script creates a local ignored checkout at:

`.vendor/place-il-quests`

The Stage 1 starter kit will then be available at:

`.vendor/place-il-quests/Quest 4/Stage 1/starter-kit`

This local checkout must never be committed to this repository.

## Supplied verification

From the repository root, after bootstrap:

```bash
python3 ".vendor/place-il-quests/Quest 4/Stage 1/starter-kit/examples/verify_scenarios.py"
```

Expected supplied behavior is 33 passing checks.

## Implementation integration

The application should access the supplied `mock_services.py` without modifying it.

Keep the starter-kit path configurable (for example `QUEST4_STARTER_KIT_PATH`) so graders/developers can point the project at their own authorized copy.

Do not silently duplicate upstream business rules into this repository.
