# Godot engine guide (Harness)

Engine: Godot $godot_version, GDScript only (C# cannot export to the web in Godot 4). Target: web browser (desktop and mobile).

## Self-check commands (run them, read the output)
- Import assets and parse everything: `godot --headless --path . --import`
- Compile every script with autoloads: `godot --headless --path . --script "$check_scripts"`
- Run the main scene for 2 seconds: `godot --headless --path . --fixed-fps 60 --quit-after 120`
- Run all test scenarios: `godot --headless --path . --fixed-fps 60 --script "$scenario_runner" -- --scenario tests/scenarios`
- Run one scenario: `godot --headless --path . --fixed-fps 60 --script "$scenario_runner" -- --scenario tests/scenarios/<name>.json`
- The same with rendering, as the Harness runs them (GUI clicks and taps on `Control` nodes, screenshots): replace `godot --headless` with `godot-render`, for example `godot-render --path . --fixed-fps 60 --script "$scenario_runner" -- --scenario tests/scenarios`. Headless runs skip GUI input handling, so a click scenario can pass rendered and fail headless.

Scratch directory for throwaway files (test scripts, notes, experiments): `$scratch`. Do not put temporary files in the game directory.

Godot often exits with code 0 even after errors. Treat any line starting with `ERROR:`, `SCRIPT ERROR:` or `Parse Error` as a failure. The Harness checks exactly these lines.

## Project rules
- Renderer is Compatibility (WebGL 2). Do not switch to Forward+ or Mobile.
- The web export is single-threaded: no `Thread`, no `WorkerThreadPool`.
- Keep `export_presets.cfg` and its `Web` preset. The Harness exports the game and opens it in Chromium on a desktop and on an emulated phone: the page must load with no console errors (every `push_error` and engine error counts), a tap must switch `Platform.touch_mode` on, and audio must be muted while the page is hidden. `Platform` already does the muting through page events; do not remove that code.
- Audio on the web: no AudioEffects on buses (reverb, delay and similar are unsupported). Browsers start audio only after the first user input.
- Static typing is required: `untyped_declaration` is an error. Write `var speed: float = 200.0`, `func f(x: int) -> void:`. Avoid `:=` when the right side is a Variant (dictionary access, `get()`, untyped array element): inference fails to compile.
- Autoloads already present: `Platform` (saves, language, focus/pause signals; always use `Platform.save_data()` / `Platform.load_data()` for progress) and `Game` (game-wide state; keep `game_state()` returning a Dictionary that describes the current game).
- Call `Platform.loading_ready()` once when the game is playable. Pause gameplay and audio on `Platform.pause_requested`, resume on `Platform.resume_requested`.
- Languages: every text the player sees is in English and Russian. Put it in `locale/strings.csv` (`keys,en,ru`; already registered in `project.godot`) and show it with `tr("KEY")`, or set the key as a Label/Button text (they translate automatically). `Platform` picks the language at start. Leave room in the layout: Russian text is often 30% longer. The Harness fails the check when a key lacks `en` or `ru` text, and opens the game on a Russian phone.
- Input: define actions in `project.godot` `[input]` and read them with `Input.is_action_pressed("name")`. Every action must also work by touch (on-screen buttons or gestures) because mobile browsers have no keyboard.
- On-screen touch controls are visible only in touch mode: show them when `Platform.touch_mode` is true, and update on `Platform.input_mode_changed`. A desktop player with keyboard and mouse must not see them.
- Layout: base resolution 1280x720, stretch mode `canvas_items`, aspect `expand`. UI must stay inside the screen at 16:9, 9:16 and 4:3 (use anchors and containers).
- `.tscn` files are text: keep `ext_resource` ids and `load_steps` consistent. When a scene is complex, build nodes from code in `_ready()` instead of writing long `.tscn` files by hand.
- Commit the `.uid` files Godot creates next to scripts and resources. Never commit `.godot/`.
- Every image, sound, model or font file you add goes into `docs/ASSETS.md` (file, source, license); the Harness fails the check for unlisted files. Files you write yourself (SVG, generated WAV) use the license `original`.
- Graphics without external assets: shapes from `_draw()`, `Polygon2D`, `ColorRect`, `StyleBoxFlat`, or SVG files you write yourself. Give every placeholder its final size in pixels.

## Game Contract and test scenarios
Tests never look at node names. They use the contract in `docs/CONTRACT.yaml`, which you keep in sync with the game:
- `input_actions`: every InputMap action the game reads.
- `state`: what `Game.game_state()` returns, as dot paths with types (`string`, `int`, `float`, `bool`, `vec2`, `vec3`, `list`, `dict`, `any`). Include what proves the game works: current screen (`scene`), player position and alive/lives, score, level, win/lose flags, paused.
- `commands`: debug commands handled by `Game.harness_command(command, args) -> bool` (return true when handled). Add the ones tests need to reach a situation quickly, for example `set_level`, `kill_player`, `add_score`. Commands must only change state, never fake a result.

Scenarios are JSON files in `tests/scenarios/`, one behaviour each. Write one for every mechanic and for the core loop (start, play, lose or win, restart). The Harness runs them on every check, with real rendering, and also takes screenshots.

```json
{
  "description": "holding right moves the player; falling into the pit ends the run",
  "seed": 1,
  "viewport": [1280, 720],
  "steps": [
    {"wait": 30},
    {"hold": "move_right", "frames": 60},
    {"assert": ["state.player.position.x > 400", "state.player.alive"]},
    {"press": "jump"},
    {"tap": [640, 360]},
    {"click": [100, 650]},
    {"key": "ESCAPE"},
    {"command": "kill_player", "args": []},
    {"wait_until": "state.scene == 'game_over'", "timeout": 120},
    {"snapshot": "after_death"},
    {"screenshot": "game_over"}
  ]
}
```

- Frames run at a fixed 60 per second. `press` and `hold` use InputMap action names; `key` uses key names (`SPACE`, `LEFT`, `ESCAPE`); `tap` is a touch, `click` a left mouse click, both in viewport pixels.
- `assert` and `wait_until` are Godot `Expression`s over `state` (the result of `game_state()`): `state.score >= 10`, `state.player.position.x > 100`, `state.items.size() == 3`. Engine singletons are not available in expressions.
- A scenario fails on a false assert, a `wait_until` timeout, an unknown action or command, or any `ERROR:` line from the engine while it runs.
- Use `seed` (global RNG) when the scenario depends on randomness; use `randf()`/`randi()` in the game rather than a separately seeded RandomNumberGenerator.
