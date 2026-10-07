Game idea from the user:

$idea

Task: build a playable prototype of this game.

Done means:
- The core loop works end to end: start screen or immediate start, playing, a lose or win condition, restart.
- Controls work with keyboard and mouse, and with touch.
- Placeholder graphics are clean and readable (no default grey boxes without meaning), sized as the final art would be.
- Use 2D unless the idea clearly asks for 3D.
- `docs/CONTRACT.yaml` lists the input actions, the `game_state()` keys and the debug commands of the game.
- `tests/scenarios/` has a scenario for each mechanic and one for the whole core loop (start, play, lose or win, restart), and they all pass.
- All self-check commands from the engine guide report no errors.
- `docs/PROGRESS.md` describes the game, the controls, what is done and what is missing.
