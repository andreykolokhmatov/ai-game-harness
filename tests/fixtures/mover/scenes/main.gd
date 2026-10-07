extends Node2D

const SPEED: float = 300.0

@onready var dot: Node2D = $Dot


func _ready() -> void:
	Game.player = dot


func _process(delta: float) -> void:
	if Input.is_action_pressed("move_right"):
		dot.position.x += SPEED * delta


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventScreenTouch and (event as InputEventScreenTouch).pressed:
		Game.taps += 1
