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
## False while the page is hidden or the window has lost focus.
var focused: bool = true

# JavaScriptBridge callbacks must stay referenced, or the browser calls freed objects.
var _js_callbacks: Array[JavaScriptObject] = []


func _ready() -> void:
	touch_mode = DisplayServer.is_touchscreen_available()
	# UI text comes from locale/strings.csv through tr(); the browser or OS language picks the column.
	TranslationServer.set_locale(language())
	if OS.has_feature("web"):
		_listen_to_page()


func _input(event: InputEvent) -> void:
	# Touch emulated from the mouse has DEVICE_ID_EMULATION: that is still a desktop player.
	if event is InputEventScreenTouch and event.device != InputEvent.DEVICE_ID_EMULATION:
		_set_touch_mode(true)
	elif event is InputEventKey or event is InputEventJoypadButton:
		_set_touch_mode(false)


# Desktop builds get these notifications. The web build does not: there the page events
# from _listen_to_page() do the same job.
func _notification(what: int) -> void:
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		_set_focused(false)
	elif what == NOTIFICATION_APPLICATION_FOCUS_IN:
		_set_focused(true)


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


func _listen_to_page() -> void:
	var window: JavaScriptObject = JavaScriptBridge.get_interface("window")
	var document: JavaScriptObject = JavaScriptBridge.get_interface("document")
	var on_visibility: JavaScriptObject = JavaScriptBridge.create_callback(_on_page_visibility)
	var on_blur: JavaScriptObject = JavaScriptBridge.create_callback(_on_page_blur)
	var on_focus: JavaScriptObject = JavaScriptBridge.create_callback(_on_page_focus)
	_js_callbacks.assign([on_visibility, on_blur, on_focus])
	document.addEventListener("visibilitychange", on_visibility)
	window.addEventListener("blur", on_blur)
	window.addEventListener("focus", on_focus)


func _on_page_visibility(_args: Array) -> void:
	var document: JavaScriptObject = JavaScriptBridge.get_interface("document")
	_set_focused(not bool(document.hidden))


func _on_page_blur(_args: Array) -> void:
	_set_focused(false)


func _on_page_focus(_args: Array) -> void:
	_set_focused(true)


func _set_focused(value: bool) -> void:
	if value == focused:
		return
	focused = value
	_set_muted(not value)
	if value:
		resume_requested.emit("focus_gained")
	else:
		pause_requested.emit("focus_lost")


func _set_touch_mode(value: bool) -> void:
	if value != touch_mode:
		touch_mode = value
		input_mode_changed.emit(value)


func _set_muted(muted: bool) -> void:
	AudioServer.set_bus_mute(AudioServer.get_bus_index("Master"), muted)
