from typing import Literal

from pydantic import BaseModel, Field

CheckStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str = Field(description="Package version")
    git_sha: str = Field(description="Deployed commit; the post-deploy smoke test checks it")


class ReadinessResponse(BaseModel):
    status: CheckStatus
    checks: dict[str, CheckStatus]
