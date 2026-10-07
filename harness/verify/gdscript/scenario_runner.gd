extends SceneTree
## Owned by the Harness. Plays one scenario (JSON) against the game's main scene:
## injects input frame by frame, evaluates assertions on Game.game_state() with
## Godot's Expression (no access to engine singletons), saves screenshots.
##
## godot [--headless] --path <game> --fixed-fps 60 --script <this file> -- \
##     --scenario <file.json | directory> [--out <result.json>] [--shots <dir>] [--id <id>]
##
## With a directory, every *.json in it runs in its own Godot process.
## Output lines start with HARNESS_SCENARIO. Exit code: 0 pass, 1 fail, 2 bad scenario.

const SCHEMA_VERSION: int = 1
const DEFAULT_SETTLE_FRAMES: int = 5
const DEFAULT_TIMEOUT_FRAMES: int = 3600
const DEFAULT_PRESS_FRAMES: int = 2
const DEFAULT_WAIT_UNTIL_FRAMES: int = 600
const STEP_KINDS: PackedStringArray = [
	"wait", "press", "hold", "key", "click", "tap", "command", "wait_until", "assert", "snapshot", "screenshot",
]

var _args: Dictionary = {}
var _scenario: Dictionary = {}
var _id: String = ""
var _frame: int = 0
var _timeout_frames: int = DEFAULT_TIMEOUT_FRAMES
var _game: Node = null
var _steps_log: Array[Dictionary] = []
var _snapshots: Dictionary = {}
var _screenshots: Array[Dictionary] = []
var _headless: bool = false


func _initialize() -> void:
	_args = _parse_args(OS.get_cmdline_user_args())
	_headless = DisplayServer.get_name() == "headless"
	var target: String = str(_args.get("scenario", ""))
	if target.is_empty():
		_finish_invalid("missing --scenario <file.json | directory>")
		return
	var path: String = _resolve(target)
	if DirAccess.dir_exists_absolute(path):
		_run_directory(path)
		return
	call_deferred("_run_file", path)


# ---- directory mode -----------------------------------------------------

func _run_directory(dir_path: String) -> void:
	var files: PackedStringArray = []
	for file: String in DirAccess.get_files_at(dir_path):
		if file.get_extension() == "json":
			files.append(dir_path.path_join(file))
	files.sort()
	if files.is_empty():
		print("HARNESS_SCENARIO NONE no *.json scenarios in %s" % dir_path)
		quit(2)
		return
	var failed: int = 0
	for file: String in files:
		var child_args: PackedStringArray = []
		if _headless:
			child_args.append("--headless")
		child_args.append_array(["--path", ProjectSettings.globalize_path("res://"), "--fixed-fps", "60"])
		child_args.append_array(["--script", get_script().resource_path, "--", "--scenario", file])
		var output: Array = []
		var code: int = OS.execute(OS.get_executable_path(), child_args, output, true)
		for chunk: Variant in output:
			print(str(chunk).strip_edges())
		if code != 0:
			failed += 1
	print("HARNESS_SCENARIO SUMMARY total=%d failed=%d" % [files.size(), failed])
	quit(1 if failed > 0 else 0)


# ---- one scenario -------------------------------------------------------

func _run_file(path: String) -> void:
	var load_error: String = _load_scenario(path)
	if not load_error.is_empty():
		_finish_invalid(load_error)
		return
	seed(int(_scenario.get("seed", 0)))
	_game = root.get_node_or_null("Game")

	var main_path: String = str(ProjectSettings.get_setting("application/run/main_scene", ""))
	var packed: PackedScene = load(main_path) as PackedScene if not main_path.is_empty() else null
	if packed == null:
		_finish(false, -1, "cannot load main scene '%s'" % main_path)
		return
	var scene: Node = packed.instantiate()
	root.add_child(scene)
	current_scene = scene
	await _frames(int(_scenario.get("settle_frames", DEFAULT_SETTLE_FRAMES)))

	var steps: Array = _scenario["steps"]
	for i: int in steps.size():
		var step: Dictionary = steps[i]
		var message: String = await _run_step(step)
		var ok: bool = message.is_empty()
		_steps_log.append({"index": i, "step": step, "ok": ok, "frame": _frame, "message": message})
		if not ok:
			_finish(false, i, message)
			return
		if _frame > _timeout_frames:
			_finish(false, i, "scenario exceeded timeout_frames=%d" % _timeout_frames)
			return
	_finish(true, -1, "")


## Returns "" on success, otherwise the failure message.
func _run_step(step: Dictionary) -> String:
	var kind: String = _kind_of(step)
	match kind:
		"wait":
			await _frames(int(step["wait"]))
		"press", "hold":
			var action: String = str(step[kind])
			if not InputMap.has_action(action):
				return "input action '%s' is not in the InputMap; known actions: %s" % [action, _game_actions()]
			var frames: int = int(step.get("frames", DEFAULT_PRESS_FRAMES))
			_send_action(action, true)
			await _frames(frames)
			_send_action(action, false)
			await _frames(1)
		"key":
			var keycode: Key = OS.find_keycode_from_string(str(step["key"]))
			if keycode == KEY_NONE:
				return "unknown key '%s' (use names like SPACE, LEFT, A, ENTER)" % step["key"]
			_send_key(keycode, true)
			await _frames(int(step.get("frames", DEFAULT_PRESS_FRAMES)))
			_send_key(keycode, false)
			await _frames(1)
		"click", "tap":
			var pos: Variant = _vector(step[kind])
			if pos == null:
				return "%s needs [x, y] in viewport pixels" % kind
			if kind == "click":
				await _click(pos)
			else:
				await _tap(pos)
		"command":
			return await _command(str(step["command"]), step.get("args", []))
		"wait_until":
			var limit: int = int(step.get("timeout", DEFAULT_WAIT_UNTIL_FRAMES))
			var waited: int = 0
			while true:
				var check: Array = _evaluate(str(step["wait_until"]))
				if not str(check[1]).is_empty():
					return check[1]
				if check[0] == true:
					break
				if waited >= limit:
					return "wait_until '%s' still false after %d frames; state: %s" % [
						step["wait_until"], limit, JSON.stringify(_json(_state()))]
				await _frames(1)
				waited += 1
		"assert":
			var exprs: Array = step["assert"] if step["assert"] is Array else [step["assert"]]
			for expr: Variant in exprs:
				var check: Array = _evaluate(str(expr))
				if not str(check[1]).is_empty():
					return check[1]
				if check[0] != true:
					return "assert failed: %s; state: %s" % [expr, JSON.stringify(_json(_state()))]
		"snapshot":
			_snapshots[str(step["snapshot"])] = _json(_state())
		"screenshot":
			await _screenshot(str(step["screenshot"]))
		_:
			return "unknown step %s; known kinds: %s" % [JSON.stringify(step), ", ".join(STEP_KINDS)]
	return ""


# ---- input --------------------------------------------------------------

func _send_action(action: String, pressed: bool) -> void:
	var event: InputEventAction = InputEventAction.new()
	event.action = action
	event.pressed = pressed
	event.strength = 1.0 if pressed else 0.0
	Input.parse_input_event(event)


func _send_key(keycode: Key, pressed: bool) -> void:
	var event: InputEventKey = InputEventKey.new()
	event.keycode = keycode
	event.physical_keycode = keycode
	event.pressed = pressed
	Input.parse_input_event(event)


func _click(pos: Vector2) -> void:
	var motion: InputEventMouseMotion = InputEventMouseMotion.new()
	motion.position = pos
	motion.global_position = pos
	Input.parse_input_event(motion)
	var button: InputEventMouseButton = InputEventMouseButton.new()
	button.position = pos
	button.global_position = pos
	button.button_index = MOUSE_BUTTON_LEFT
	button.pressed = true
	Input.parse_input_event(button)
	await _frames(DEFAULT_PRESS_FRAMES)
	var release: InputEventMouseButton = button.duplicate() as InputEventMouseButton
	release.pressed = false
	Input.parse_input_event(release)
	await _frames(1)


func _tap(pos: Vector2) -> void:
	var touch: InputEventScreenTouch = InputEventScreenTouch.new()
	touch.index = 0
	touch.position = pos
	touch.pressed = true
	Input.parse_input_event(touch)
	await _frames(DEFAULT_PRESS_FRAMES)
	var release: InputEventScreenTouch = touch.duplicate() as InputEventScreenTouch
	release.pressed = false
	Input.parse_input_event(release)
	await _frames(1)


func _game_actions() -> String:
	var names: PackedStringArray = []
	for action: StringName in InputMap.get_actions():
		if not str(action).begins_with("ui_"):
			names.append(str(action))
	return "[%s]" % ", ".join(names)


# ---- game state, commands, expressions ----------------------------------

func _state() -> Variant:
	if _game == null or not _game.has_method("game_state"):
		return null
	return _game.call("game_state")


func _command(name: String, args: Variant) -> String:
	if _game == null or not _game.has_method("harness_command"):
		return "Game.harness_command(name, args) is not defined (see docs/CONTRACT.yaml commands)"
	var arg_list: Array = args if args is Array else [args]
	var handled: Variant = _game.call("harness_command", name, arg_list)
	if handled is Object and (handled as Object).get_class() == "GDScriptFunctionState":
		handled = await handled
	if handled != true:
		return "command '%s' %s was not handled (Game.harness_command returned %s)" % [name, JSON.stringify(arg_list), handled]
	await _frames(1)
	return ""


## [value, error message or ""]
func _evaluate(source: String) -> Array:
	var state: Variant = _state()
	if state == null:
		return [null, "Game.game_state() is not available (autoload 'Game' with game_state() -> Dictionary)"]
	var expression: Expression = Expression.new()
	var parse_error: Error = expression.parse(source, PackedStringArray(["state"]))
	if parse_error != OK:
		return [null, "cannot parse expression '%s': %s" % [source, expression.get_error_text()]]
	var value: Variant = expression.execute([state], null, false)
	if expression.has_execute_failed():
		return [null, "expression '%s' failed: %s; state: %s" % [source, expression.get_error_text(), JSON.stringify(_json(state))]]
	return [value, ""]


# ---- screenshots ----------------------------------------------------------

func _screenshot(label: String) -> void:
	var shots: String = str(_args.get("shots", ""))
	if _headless or shots.is_empty():
		return
	await RenderingServer.frame_post_draw
	var image: Image = root.get_texture().get_image()
	if image == null:
		print("HARNESS_SCENARIO WARN screenshot '%s': no image" % label)
		return
	DirAccess.make_dir_recursive_absolute(shots)
	var file: String = shots.path_join("%s__%s.png" % [_id, label.validate_filename()])
	if image.save_png(file) == OK:
		_screenshots.append({"label": label, "file": file, "size": [image.get_width(), image.get_height()],
			"distinct_colors": _distinct_colors(image)})


## Distinct colors in a 160x160 averaged copy: 1 means a blank (single-color) frame.
## Averaging keeps small details such as a line of text visible in the count.
func _distinct_colors(image: Image) -> int:
	var small: Image = image.duplicate() as Image
	small.resize(160, 160, Image.INTERPOLATE_BILINEAR)
	var seen: Dictionary = {}
	for y: int in small.get_height():
		for x: int in small.get_width():
			seen[small.get_pixel(x, y).to_rgba32()] = true
	return seen.size()


# ---- loading and results --------------------------------------------------

func _load_scenario(path: String) -> String:
	if not FileAccess.file_exists(path):
		return "scenario file not found: %s" % path
	var json: JSON = JSON.new()
	if json.parse(FileAccess.get_file_as_string(path)) != OK:
		return "%s: invalid JSON at line %d: %s" % [path, json.get_error_line(), json.get_error_message()]
	if not json.data is Dictionary:
		return "%s: top level must be an object" % path
	_scenario = json.data
	_id = str(_args.get("id", _scenario.get("id", path.get_file().get_basename())))
	_timeout_frames = int(_scenario.get("timeout_frames", DEFAULT_TIMEOUT_FRAMES))
	var steps: Variant = _scenario.get("steps")
	if not steps is Array or (steps as Array).is_empty():
		return "%s: 'steps' must be a non-empty array" % path
	for i: int in (steps as Array).size():
		var step: Variant = (steps as Array)[i]
		if not step is Dictionary or _kind_of(step).is_empty():
			return "%s: step %d %s has no known kind (%s)" % [path, i, JSON.stringify(step), ", ".join(STEP_KINDS)]
	return ""


func _kind_of(step: Dictionary) -> String:
	for kind: String in STEP_KINDS:
		if step.has(kind):
			return kind
	return ""


func _finish(passed: bool, failed_step: int, message: String) -> void:
	var result: Dictionary = {
		"schema_version": SCHEMA_VERSION,
		"id": _id,
		"description": _scenario.get("description", ""),
		"covers": _scenario.get("covers", []),
		"passed": passed,
		"failed_step": failed_step,
		"message": message,
		"frames": _frame,
		"headless": _headless,
		"steps": _steps_log,
		"snapshots": _snapshots,
		"final_state": _json(_state()),
		"screenshots": _screenshots,
	}
	_write_result(result)
	if passed:
		print("HARNESS_SCENARIO PASS %s frames=%d" % [_id, _frame])
	else:
		print("HARNESS_SCENARIO FAIL %s step=%d %s" % [_id, failed_step, message])
	quit(0 if passed else 1)


func _finish_invalid(message: String) -> void:
	_write_result({"schema_version": SCHEMA_VERSION, "id": _id, "passed": false, "invalid": true, "message": message})
	print("HARNESS_SCENARIO INVALID %s" % message)
	quit(2)


func _write_result(result: Dictionary) -> void:
	var out: String = str(_args.get("out", ""))
	if out.is_empty():
		return
	DirAccess.make_dir_recursive_absolute(out.get_base_dir())
	var file: FileAccess = FileAccess.open(out, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify(result, "\t"))


# ---- helpers --------------------------------------------------------------

func _frames(count: int) -> void:
	for _i: int in maxi(count, 0):
		await process_frame
		_frame += 1


func _vector(value: Variant) -> Variant:
	if value is Array and (value as Array).size() == 2:
		return Vector2(float(value[0]), float(value[1]))
	return null


func _resolve(path: String) -> String:
	if path.begins_with("res://") or path.begins_with("user://"):
		return ProjectSettings.globalize_path(path)
	if path.is_absolute_path():
		return path
	return ProjectSettings.globalize_path("res://").path_join(path)


func _parse_args(raw: PackedStringArray) -> Dictionary:
	var parsed: Dictionary = {}
	var i: int = 0
	while i < raw.size():
		var arg: String = raw[i]
		if arg.begins_with("--") and i + 1 < raw.size():
			parsed[arg.substr(2)] = raw[i + 1]
			i += 2
		else:
			i += 1
	return parsed


## Converts engine types (vectors, colors, string names) into JSON-friendly values.
func _json(value: Variant) -> Variant:
	match typeof(value):
		TYPE_DICTIONARY:
			var out: Dictionary = {}
			for key: Variant in (value as Dictionary):
				out[str(key)] = _json((value as Dictionary)[key])
			return out
		TYPE_ARRAY:
			var items: Array = []
			for item: Variant in (value as Array):
				items.append(_json(item))
			return items
		TYPE_VECTOR2, TYPE_VECTOR2I:
			return [value.x, value.y]
		TYPE_VECTOR3, TYPE_VECTOR3I:
			return [value.x, value.y, value.z]
		TYPE_COLOR:
			return [value.r, value.g, value.b, value.a]
		TYPE_STRING_NAME, TYPE_NODE_PATH:
			return str(value)
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STRING:
			return value
		_:
			return var_to_str(value)
