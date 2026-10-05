from harness.gitops.checkpoints import GameRepo


def test_commit_tag_and_reset(tmp_path):
    repo = GameRepo(tmp_path / "repo")
    repo.init()
    (repo.path / ".gitignore").write_text(".godot/\n", encoding="utf-8")
    (repo.path / "a.txt").write_text("1", encoding="utf-8")
    first = repo.commit_all("first")
    assert first and repo.is_clean()
    assert repo.commit_all("nothing") is None
    repo.tag("cp/created")

    (repo.path / "a.txt").write_text("2", encoding="utf-8")
    (repo.path / "new.txt").write_text("x", encoding="utf-8")
    (repo.path / ".godot").mkdir()
    (repo.path / ".godot" / "cache").write_text("c", encoding="utf-8")
    assert not repo.is_clean()

    repo.reset_hard("cp/created")
    assert repo.is_clean()
    assert (repo.path / "a.txt").read_text(encoding="utf-8") == "1"
    assert not (repo.path / "new.txt").exists()
    assert (repo.path / ".godot" / "cache").exists()  # ignored cache survives
    assert repo.tag_sha("cp/created") == first
    assert repo.tag_sha("cp/missing") is None
