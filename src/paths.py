"""
Where the application lives on disk.

Every configured path is anchored here rather than against the working directory:
the CLI is normally started from the repo root, but a `uvicorn` process is not, and
a relative database path would silently create a second, empty database wherever it
happened to be launched from.
"""

from pathlib import Path

# The directory containing `src/`, i.e. the repository root (`/app` in the image).
REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_path(value: str | Path) -> Path:
    """
    Anchor a configured path against the repo root.

    An absolute path is returned unchanged and a leading `~` is expanded, so a user
    can file books outside the repo. The result is deliberately not `resolve()`d:
    the audiobook folder is a bind mount under Docker and frequently a symlink to a
    network share otherwise, and following those links helps nobody.

    Args:
        value: Path from the configuration, absolute or relative

    Returns:
        An absolute path.
    """
    path = Path(value).expanduser()
    return path if path.is_absolute() else REPO_ROOT / path
