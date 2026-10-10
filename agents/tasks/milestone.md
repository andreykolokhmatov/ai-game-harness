Build milestone $milestone_id of the game: $title

Goal: $goal

Acceptance criteria (an independent Evaluator checks each one after you finish):
$criteria

Read `docs/GDD.md` (the design), `docs/PLAN.md` (all milestones) and `docs/CONTRACT.yaml` (the Game Contract) first. $context

Done means:
- Every acceptance criterion above holds, and `tests/scenarios/` has a scenario that proves it. Name the criterion in the scenario `description`, for example `"AC-3: falling into a gap costs a life"`.
- The Game Contract in `docs/CONTRACT.yaml` is implemented: every input action, every `game_state()` key with its type, every debug command. You may add keys, actions and commands; do not remove or rename the planned ones.
- Controls work with keyboard and mouse, and with touch.
- Graphics are clean and readable (no default grey boxes without meaning), sized as the final art would be.
- All self-check commands from the engine guide report no errors, including all scenarios.
- `docs/PROGRESS.md` describes the game, the controls, what is done and what is missing.
