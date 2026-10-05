extends Node
## Platform facade. Games talk to the hosting platform only through this node.
## MVP backend: mock (local saves, browser/OS focus). A yandex backend comes later.

signal pause_requested(reason: String)
signal resume_requested(reason: String)
## Emitted when the player switches between touch and keyboard/mouse.
signal input_mode_changed(touch: bool)

const SAVE_PATH: String = "user://save.json"

var backend: String = "mock"
## True when on-screen touch controls should be shown.
var touch_mode: bool = false


func _ready() -> void:
	touch_mode = DisplayServer.is_touchscreen_available()


func _input(event: InputEvent) -> void:
	# Touch emulated from the mouse has DEVICE_ID_EMULATION: that is still a desktop player.
	if event is InputEventScreenTouch and event.device != InputEvent.DEVICE_ID_EMULATION:
		_set_touch_mode(true)
	elif event is InputEventKey or event is InputEventJoypadButton:
		_set_touch_mode(false)


func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		_set_muted(true)
		pause_requested.emit("focus_lost")
	elif what == NOTIFICATION_APPLICATION_FOCUS_IN:
		_set_muted(false)
		resume_requested.emit("focus_gained")


## Call once the game has loaded and can be played.
func loading_ready() -> void:
	pass


func gameplay_start() -> void:
	pass


func gameplay_stop() -> void:
	pass


## Two-letter language code (ISO 639-1). Falls back to "en".
func language() -> String:
	var lang: String = OS.get_locale_language()
	return lang if lang in ["en", "ru"] else "en"


func save_data(data: Dictionary) -> void:
	var file: FileAccess = FileAccess.open(SAVE_PATH, FileAccess.WRITE)
	if file == null:
		push_error("Platform: cannot write save: %s" % error_string(FileAccess.get_open_error()))
		return
	file.store_string(JSON.stringify(data))


func load_data() -> Dictionary:
	if not FileAccess.file_exists(SAVE_PATH):
		return {}
	var text: String = FileAccess.get_file_as_string(SAVE_PATH)
	var parsed: Variant = JSON.parse_string(text)
	return parsed if parsed is Dictionary else {}


func _set_touch_mode(value: bool) -> void:
	if value != touch_mode:
		touch_mode = value
		input_mode_changed.emit(value)


func _set_muted(muted: bool) -> void:
	AudioServer.set_bus_mute(AudioServer.get_bus_index("Master"), muted)
