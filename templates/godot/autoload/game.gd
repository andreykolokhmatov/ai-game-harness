extends Node
## Game-wide state. The Engineer extends this file.
## game_state() must return the keys listed in docs/CONTRACT.yaml (stage 3+).


func game_state() -> Dictionary:
	return {"scene": get_tree().current_scene.name if get_tree().current_scene else ""}
