"""Countries & languages: the script language and destination phrase of each country."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from pydantic import Field, computed_field

from lanereach.auth.dependencies import TenantSession, require_scopes
from lanereach.auth.principal import Principal
from lanereach.auth.roles import Scope
from lanereach.core.dependencies import ClockDependency
from lanereach.core.languages import LANGUAGE_VARIANT_PATTERN, language_name
from lanereach.core.openapi import READ, WRITE
from lanereach.core.schemas import ResponseModel, StrictRequestModel
from lanereach.domain.countries.service import CountryService

router = APIRouter(prefix="/countries", tags=["Countries & languages"])

Reader = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_READ))]
Writer = Annotated[Principal, Depends(require_scopes(Scope.WORKFLOWS_WRITE))]
CountryCode = Annotated[str, Path(pattern=r"^[A-Z]{2}$", examples=["TR"])]


class CountryRequest(StrictRequestModel):
    """How LaneReach writes to a country."""

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"name": "France", "language": "fr", "to_country_phrase": "vers la France"}
            ]
        }
    }

    name: str = Field(min_length=1, max_length=100)
    language: str = Field(pattern=LANGUAGE_VARIANT_PATTERN, description="Script language.")
    to_country_phrase: str = Field(
        min_length=1, max_length=200, description="Destination as written in that language."
    )
    allow_language_fallback: bool = Field(
        default=True,
        description="When false, emails are held until a script exists in this language.",
    )
    is_enabled: bool = True


class CountryResponse(ResponseModel):
    """A country's row."""

    id: uuid.UUID
    country: str
    name: str
    language: str
    to_country_phrase: str
    allow_language_fallback: bool
    is_enabled: bool
    updated_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def language_name(self) -> str:
        """Display name of the script language."""
        return language_name(self.language)


def get_country_service(session: TenantSession, clock: ClockDependency) -> CountryService:
    """Country service for the request."""
    return CountryService(session, clock)


Service = Annotated[CountryService, Depends(get_country_service)]


@router.get("", response_model=list[CountryResponse], summary="All countries", responses=READ)
async def list_countries(_principal: Reader, service: Service) -> list[CountryResponse]:
    """Countries by language."""
    return [CountryResponse.model_validate(row) for row in await service.list_countries()]


@router.put(
    "/{country}", response_model=CountryResponse, summary="Add or change a country", responses=WRITE
)
async def upsert_country(
    country: CountryCode, payload: CountryRequest, _principal: Writer, service: Service
) -> CountryResponse:
    """Create or replace a country's row (idempotent by nature)."""
    return CountryResponse.model_validate(
        await service.upsert_country(country, **payload.model_dump())
    )


@router.delete(
    "/{country}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a country",
    responses=WRITE,
)
async def delete_country(country: CountryCode, _principal: Writer, service: Service) -> None:
    """Remove a country (its contacts then get their own language, else English)."""
    await service.delete_country(country)
