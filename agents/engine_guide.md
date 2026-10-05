# Godot engine guide (Harness)

Engine: Godot $godot_version, GDScript only (C# cannot export to the web in Godot 4). Target: web browser (desktop and mobile).

## Self-check commands (run them, read the output)
- Import assets and parse everything: `godot --headless --path . --import`
- Compile every script with autoloads: `godot --headless --path . --script "$check_scripts"`
- Run the main scene for 2 seconds: `godot --headless --path . --fixed-fps 60 --quit-after 120`

Godot often exits with code 0 even after errors. Treat any line starting with `ERROR:`, `SCRIPT ERROR:` or `Parse Error` as a failure. The Harness checks exactly these lines.

## Project rules
- Renderer is Compatibility (WebGL 2). Do not switch to Forward+ or Mobile.
- The web export is single-threaded: no `Thread`, no `WorkerThreadPool`.
- Audio on the web: no AudioEffects on buses (reverb, delay and similar are unsupported). Browsers start audio only after the first user input.
- Static typing is required: `untyped_declaration` is an error. Write `var speed: float = 200.0`, `func f(x: int) -> void:`. Avoid `:=` when the right side is a Variant (dictionary access, `get()`, untyped array element): inference fails to compile.
- Autoloads already present: `Platform` (saves, language, focus/pause signals; always use `Platform.save_data()` / `Platform.load_data()` for progress) and `Game` (game-wide state; keep `game_state()` returning a Dictionary that describes the current game).
- Call `Platform.loading_ready()` once when the game is playable. Pause gameplay and audio on `Platform.pause_requested`, resume on `Platform.resume_requested`.
- Input: define actions in `project.godot` `[input]` and read them with `Input.is_action_pressed("name")`. Every action must also work by touch (on-screen buttons or gestures) because mobile browsers have no keyboard.
- Layout: base resolution 1280x720, stretch mode `canvas_items`, aspect `expand`. UI must stay inside the screen at 16:9, 9:16 and 4:3 (use anchors and containers).
- `.tscn` files are text: keep `ext_resource` ids and `load_steps` consistent. When a scene is complex, build nodes from code in `_ready()` instead of writing long `.tscn` files by hand.
- Commit the `.uid` files Godot creates next to scripts and resources. Never commit `.godot/`.
- Graphics without external assets: shapes from `_draw()`, `Polygon2D`, `ColorRect`, `StyleBoxFlat`, or SVG files you write yourself. Give every placeholder its final size in pixels.
