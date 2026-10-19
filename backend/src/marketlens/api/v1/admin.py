"""Admin actions. The API never does the heavy work itself (rule 5): retry ENQUEUES the
Celery task; promote only flips stages through the same gate the scheduler uses."""

import uuid

from fastapi import APIRouter, Request, status

from marketlens.api.deps import AdminUser
from marketlens.api.errors import ApiError
from marketlens.schemas.auth import PromoteIn, PromoteOut, RetryOut
from marketlens.schemas.common import ERROR_RESPONSES
from marketlens.services.admin import plan_retry
from marketlens.services.promotion import PromotionService

router = APIRouter(prefix="/admin", tags=["admin"], responses=ERROR_RESPONSES)


@router.post("/runs/{run_id}/retry", response_model=RetryOut, status_code=status.HTTP_202_ACCEPTED)
async def retry_run(run_id: uuid.UUID, request: Request, admin: AdminUser) -> RetryOut:
    """Re-queue a failed task run with its original parameters (202: queued, not done)."""
    from marketlens.tasks.celery_app import celery_app

    try:
        plan = await plan_retry(request.app.state.sessionmaker, run_id)
    except LookupError as exc:
        raise ApiError(404, str(exc), code="run_not_found") from exc
    except ValueError as exc:  # not a task run, or not in a retryable state
        raise ApiError(409, str(exc), code="not_retryable") from exc
    result = celery_app.send_task(plan.task_name, kwargs=plan.kwargs)
    return RetryOut(
        pipeline_run_id=plan.run_id,
        task=plan.task_name,
        params=plan.kwargs,
        celery_task_id=str(result.id),
    )


@router.post("/models/{model_version_id}/promote", response_model=PromoteOut)
async def promote(
    model_version_id: int, body: PromoteIn, request: Request, admin: AdminUser
) -> PromoteOut:
    """Promote through the gate. `force=true` overrides a failing gate, needs a `reason`, and
    is recorded as a `manual` decision with the admin's identity."""
    result = await PromotionService(request.app.state.sessionmaker).promote_version(
        model_version_id, body.force, body.reason, actor=f"admin:{admin.email}"
    )
    return PromoteOut(
        decision=result["decision"],
        reasons=result["reasons"],
        production_model_version_id=result["production"],
    )
