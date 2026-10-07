extends Node
## Game-wide state. The Engineer extends this file.
## Tests talk to the game only through these two functions (see docs/CONTRACT.yaml).


## Everything a test needs to check the game. Keys and types must match docs/CONTRACT.yaml `state`.
func game_state() -> Dictionary:
	return {"scene": get_tree().current_scene.name if get_tree().current_scene else ""}


## Debug commands for tests, listed in docs/CONTRACT.yaml `commands`.
## Return true when the command was handled, false for an unknown command.
func harness_command(command: String, args: Array) -> bool:
	match command:
		_:
			return false
