"""
The scheduler and the runs: what is happening, Sync now and Cancel, and the run history.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from audible_sync.api.schemas import (
    Message,
    RunBookOut,
    RunBooks,
    Status,
    SyncRunList,
    SyncRunOut,
)
from audible_sync.database import (
    count_sync_runs,
    get_accounts,
    get_run_books,
    get_sync_run,
    get_sync_runs,
)
from audible_sync.model import RunBookOutcome
from audible_sync.pipeline.runstate import RunState
from audible_sync.pipeline.scheduler import Scheduler
from audible_sync.settings import Settings


def router(*, scheduler: Scheduler, state: RunState) -> APIRouter:
    router = APIRouter()

    @router.get("/status", response_model=Status)
    def status() -> Status:
        settings = Settings.from_db()
        last = get_sync_runs(limit=1)
        return Status(
            scheduler=scheduler.status(settings),
            current_run=state.snapshot(),
            last_run=last[0] if last else None,
            accounts=get_accounts(),
        )

    @router.post("/sync", response_model=Message, status_code=202)
    def start_sync() -> Message:
        if not scheduler.trigger():
            raise HTTPException(status_code=409, detail="A sync is already running")
        return Message(status="started")

    @router.post("/sync/cancel", response_model=Message, status_code=202)
    def cancel_sync() -> Message:
        if not scheduler.cancel():
            raise HTTPException(status_code=409, detail="No sync is running")
        return Message(status="cancelling")

    @router.get("/sync/runs", response_model=SyncRunList)
    def list_runs(
        limit: Annotated[int, Query(ge=1, le=200)] = 20,
        offset: Annotated[int, Query(ge=0)] = 0,
        account_id: int | None = None,
    ) -> SyncRunList:
        return SyncRunList(
            items=get_sync_runs(limit=limit, offset=offset, account_id=account_id),
            total=count_sync_runs(account_id=account_id),
        )

    @router.get("/sync/runs/{run_id}", response_model=SyncRunOut)
    def get_run(run_id: int) -> SyncRunOut:
        run = get_sync_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        return run

    @router.get("/sync/runs/{run_id}/books", response_model=RunBooks)
    def get_run_books_route(run_id: int) -> RunBooks:
        """
        The books a run downloaded, failed and was refused, for the Activity screen.
        `recorded` is false on a run from before the per-book records, which says so
        rather than showing three empty lists.
        """
        run = get_sync_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        groups: dict[str, list[RunBookOut]] = {outcome.value: [] for outcome in RunBookOutcome}
        for record in get_run_books(run_id):
            if record.outcome in groups:
                groups[record.outcome].append(RunBookOut.model_validate(record))
        return RunBooks(run_id=run_id, recorded=run.books_recorded, **groups)

    return router
