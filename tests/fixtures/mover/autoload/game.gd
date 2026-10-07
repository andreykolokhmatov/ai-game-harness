extends Node

var player: Node2D = null
var taps: int = 0


func game_state() -> Dictionary:
	return {
		"scene": get_tree().current_scene.name if get_tree().current_scene else "",
		"player": {"position": player.position if player else Vector2.ZERO},
		"taps": taps,
		"touch_mode": Platform.touch_mode,
	}


func harness_command(name: String, args: Array) -> bool:
	match name:
		"teleport":
			player.position = Vector2(float(args[0]), float(args[1]))
			return true
	return false
