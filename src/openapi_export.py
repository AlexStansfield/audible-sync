"""
Write the API's OpenAPI schema to a file: `python -m src.openapi_export web/openapi.json`.

The web app's TypeScript types are generated from this file (`npm run gen:api` in
`web/`), and CI regenerates both and fails when they differ from what is committed, so
the client cannot drift from `src/schemas.py`. The app is built with stand-ins: nothing
is started, no database or key is touched, and no server has to be running.
"""

import json
import sys
from pathlib import Path

from src.api import create_app
from src.apitoken import ApiToken
from src.runstate import RunState

# A constant rather than pyproject's version, so a release bump does not make the
# committed schema stale; nothing generated from it reads the version
SCHEMA_VERSION = "0"


class _NoScheduler:
    """Never called: the scheduler is only used by the app's lifespan and its routes."""


def openapi_schema() -> dict:
    """The API's OpenAPI schema, from an app built with stand-ins; nothing is started or stored."""
    app = create_app(
        scheduler=_NoScheduler(),  # type: ignore[arg-type]
        state=RunState(),
        api_token=ApiToken("schema-export-only", "stored"),
        version=SCHEMA_VERSION,
    )
    return app.openapi()


def main(argv: list[str] | None = None) -> None:
    """Write the schema to the one path given, sorted and indented so a diff reads cleanly."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: python -m src.openapi_export OUTPUT.json")
    Path(args[0]).write_text(json.dumps(openapi_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
