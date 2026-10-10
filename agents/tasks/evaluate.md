Evaluate milestone $milestone_id of the game: $title

Goal: $goal

Acceptance criteria to judge:
$criteria

Files changed in this milestone (since $base):
$diff_stat

Run a scenario (headless, fixed 60 fps), from your working directory:
`godot --headless --path game --fixed-fps 60 --script "$scenario_runner" -- --scenario "$scenarios_dir/<name>.json"`
Run all of yours: the same command with `--scenario "$scenarios_dir"`. The scenario format is described in the engine guide. A scenario fails on a false assert, a wait_until timeout, an unknown action or command, or any engine `ERROR:` line.

Return your verdict with one entry per criterion above and every issue you found.
