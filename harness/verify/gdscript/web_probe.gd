extends Node
## Harness probe for the Web-Test build. The Harness adds it as the last autoload in a copy
## of the game at export time; the game repository never contains it.
##
## Publishes a JSON snapshot to `window.harnessState` every few frames, so the browser test
## reads it without calling into the engine (JavaScriptBridge callbacks cannot return values).
## Typed strictly: the game's project settings treat untyped declarations as errors.

const PUBLISH_EVERY_FRAMES: int = 5
const READY_AFTER_FRAMES: int = 2

var _frames: int = 0
var _window: JavaScriptObject


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS
	if not OS.has_feature("web"):
		return
	_window = JavaScriptBridge.get_interface("window")
	_publish()


func _process(_delta: float) -> void:
	_frames += 1
	if _window != null and (_frames == READY_AFTER_FRAMES or _frames % PUBLISH_EVERY_FRAMES == 0):
		_publish()


func _publish() -> void:
	var snapshot: Dictionary = {
		"ready": _frames >= READY_AFTER_FRAMES,
		"frames": _frames,
		"muted": AudioServer.is_bus_mute(AudioServer.get_bus_index("Master")),
		"locale": TranslationServer.get_locale(),
	}
	var platform: Node = get_node_or_null("/root/Platform")
	if platform != null:
		snapshot["platform"] = {"focused": platform.get("focused"), "touch_mode": platform.get("touch_mode")}
	var game: Node = get_node_or_null("/root/Game")
	if game != null and game.has_method("game_state"):
		snapshot["game"] = game.call("game_state")
	_window.harnessState = JSON.stringify(snapshot)
