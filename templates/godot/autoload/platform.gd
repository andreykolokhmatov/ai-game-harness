extends Node
## Platform facade. Games talk to the hosting platform only through this node.
## MVP backend: mock (local saves, browser/OS focus). A yandex backend comes later.

signal pause_requested(reason: String)
signal resume_requested(reason: String)

const SAVE_PATH: String = "user://save.json"

var backend: String = "mock"


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


func _set_muted(muted: bool) -> void:
	AudioServer.set_bus_mute(AudioServer.get_bus_index("Master"), muted)
