from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(
    prefix="/health",
    tags=["Health"],
)

settings = get_settings()


class HealthResponse(BaseModel):
    status: Literal["healthy", "degraded"]
    service: str
    version: str
    environment: str
    database: Literal["connected", "unavailable"]
    timestamp: datetime


@router.get(
    "",
    response_model=HealthResponse,
)
def health_check(
    database: Session = Depends(get_db),
) -> HealthResponse:
    database_status: Literal["connected", "unavailable"]
    application_status: Literal["healthy", "degraded"]

    try:
        database.execute(text("SELECT 1"))
        database_status = "connected"
        application_status = "healthy"
    except SQLAlchemyError:
        database_status = "unavailable"
        application_status = "degraded"

    return HealthResponse(
        status=application_status,
        service=settings.app_name,
        version=settings.app_version,
        environment=settings.environment,
        database=database_status,
        timestamp=datetime.now(timezone.utc),
    )