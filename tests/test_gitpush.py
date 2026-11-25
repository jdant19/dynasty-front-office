"""Scheduled jobs push without failing when another job pushed first (2026-11-22 collector failure)."""
import subprocess

from dfo import gitpush


def sh(cwd, *a):
    return subprocess.run(list(a), cwd=cwd, capture_output=True, text=True, check=True).stdout


def test_push_after_another_job_pushed(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    sh(tmp_path, "git", "init", "-q", "--bare", "-b", "main", str(origin))
    a, b = tmp_path / "a", tmp_path / "b"
    sh(tmp_path, "git", "clone", "-q", str(origin), str(a))
    for c in (a,):
        sh(c, "git", "config", "user.email", "t@t"); sh(c, "git", "config", "user.name", "t")
    (a / "data/processed").mkdir(parents=True); (a / "data/journal").mkdir(parents=True)
    (a / "data/processed/odds.csv").write_text("owner,p\nJordan,0.5\n")
    (a / "data/journal/recommendations.csv").write_text("id,text\nr1,first\n")
    (a / "data/journal/notes.csv").write_text("id\nn1\n")
    sh(a, "git", "add", "."); sh(a, "git", "commit", "-q", "-m", "base"); sh(a, "git", "push", "-q", "origin", "HEAD:main")
    sh(tmp_path, "git", "clone", "-q", str(origin), str(b))
    sh(b, "git", "config", "user.email", "t@t"); sh(b, "git", "config", "user.name", "t")
    # job A pushes first
    (a / "data/processed/odds.csv").write_text("owner,p\nJordan,0.55\n")
    (a / "data/journal/recommendations.csv").write_text("id,text\nr1,first\nr2,from A\n")
    sh(a, "git", "commit", "-qam", "A"); sh(a, "git", "push", "-q")
    # job B changed the same files from the old base
    (b / "data/processed/odds.csv").write_text("owner,p\nJordan,0.58\n")
    (b / "data/journal/recommendations.csv").write_text("id,text\nr1,first\nr3,from B\n")
    (b / "data/journal/notes.csv").write_text("id\nn1\nn2\n")   # a tracked file changed but not part of this commit
    (b / "data/processed/scratch.txt").write_text("left alone")
    monkeypatch.chdir(b)
    monkeypatch.setattr(gitpush.time, "sleep", lambda s: None)
    assert gitpush.main("B", ["data/processed/odds.csv", "data/journal/recommendations.csv"]) == 0
    assert (b / "data/journal/notes.csv").read_text() == "id\nn1\nn2\n"                       # left as it was
    sh(a, "git", "pull", "-q")
    assert (a / "data/processed/odds.csv").read_text() == "owner,p\nJordan,0.58\n"          # the newer build
    assert (a / "data/journal/recommendations.csv").read_text() == "id,text\nr1,first\nr2,from A\nr3,from B\n"  # both rows
    assert gitpush.main("nothing", ["data/processed"]) == 0                                     # no changes: fine
