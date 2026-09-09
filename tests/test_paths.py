from pathlib import Path

from src.paths import REPO_ROOT, resolve_path


def test_repo_root_is_the_directory_containing_src():
    assert (REPO_ROOT / "src" / "paths.py").is_file()
    assert (REPO_ROOT / "pyproject.toml").is_file()


def test_relative_paths_are_anchored_to_the_repo_root():
    """A relative path used to be read against the working directory, so uvicorn
    started elsewhere created an empty database somewhere else."""
    assert resolve_path("data/audible_sync.db") == REPO_ROOT / "data" / "audible_sync.db"


def test_absolute_paths_are_left_alone():
    assert resolve_path("/mnt/media/audiobooks") == Path("/mnt/media/audiobooks")


def test_home_is_expanded():
    assert resolve_path("~/audible.json") == Path.home() / "audible.json"


def test_a_path_object_is_accepted():
    assert resolve_path(Path("audiobooks")) == REPO_ROOT / "audiobooks"


def test_symlinks_are_not_followed():
    """The audiobook folder is a bind mount under Docker and often a symlink to a
    network share, so the anchored path must not be canonicalised."""
    assert resolve_path("audiobooks") == REPO_ROOT / "audiobooks"
