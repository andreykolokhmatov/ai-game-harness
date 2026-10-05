extends SceneTree
## Owned by the Harness. Compiles every .gd file in the project with autoloads
## registered, prints one HARNESS_CHECK line per failure and quits with the count.

func _initialize() -> void:
	var failures: int = 0
	var paths: PackedStringArray = _collect("res://")
	for path: String in paths:
		var script: Script = ResourceLoader.load(path, "Script", ResourceLoader.CACHE_MODE_IGNORE) as Script
		if script == null or not script.can_instantiate():
			failures += 1
			print("HARNESS_CHECK fail %s" % path)
	print("HARNESS_CHECK done checked=%d failed=%d" % [paths.size(), failures])
	quit(1 if failures > 0 else 0)


func _collect(dir_path: String) -> PackedStringArray:
	var result: PackedStringArray = []
	var dir: DirAccess = DirAccess.open(dir_path)
	if dir == null:
		return result
	for sub: String in dir.get_directories():
		if sub.begins_with("."):
			continue
		result.append_array(_collect(dir_path.path_join(sub)))
	for file: String in dir.get_files():
		if file.get_extension() == "gd":
			result.append(dir_path.path_join(file))
	return result
