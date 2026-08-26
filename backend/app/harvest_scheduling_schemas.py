from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _required_text(value: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError("Alan boş bırakılamaz")
    return cleaned


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


class HarvestRegionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    active: bool = True

    @field_validator("code")
    @classmethod
    def clean_code(cls, value: str) -> str:
        return _required_text(value).upper()

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value)


class HarvestRegionView(HarvestRegionWrite):
    id: int
    company_id: int


class CustomerHarvestRegionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: int | None = Field(default=None, gt=0)


class HarvestCalendarWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    season_code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=180)
    season_year: int = Field(ge=2000, le=2200)
    region_id: int | None = Field(default=None, gt=0)
    product_category: str | None = Field(default=None, max_length=160)
    due_date: date
    active: bool = True

    @field_validator("season_code")
    @classmethod
    def clean_season_code(cls, value: str) -> str:
        return _required_text(value).upper()

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return _required_text(value)

    @field_validator("product_category")
    @classmethod
    def clean_category(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_year(self):
        if self.due_date.year != self.season_year:
            raise ValueError("Vade tarihi sezon yılıyla aynı yılda olmalıdır")
        return self


class HarvestCalendarView(HarvestCalendarWrite):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime


class HarvestDueRuleWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(default=None, gt=0)
    region_id: int | None = Field(default=None, gt=0)
    product_category: str | None = Field(default=None, max_length=160)
    season_code: str = Field(min_length=1, max_length=60)
    priority: int = Field(default=0, ge=-10000, le=10000)
    effective_from: date | None = None
    effective_to: date | None = None
    active: bool = True

    @field_validator("season_code")
    @classmethod
    def clean_season_code(cls, value: str) -> str:
        return _required_text(value).upper()

    @field_validator("product_category")
    @classmethod
    def clean_category(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("Bitiş tarihi başlangıç tarihinden önce olamaz")
        return self


class HarvestDueRuleView(HarvestDueRuleWrite):
    id: int
    company_id: int
    # Read-only mirror of the resolver's specificity tier so the management
    # screen can show precedence without re-implementing the ranking.
    scope: int


class HarvestRuleConflictView(BaseModel):
    """One rule pair the engine cannot rank, with the context that proves it.

    Computed by replaying the resolver's own matching function, so a NULL
    filter counts as matches-all and non-overlapping validity windows are
    never reported.
    """

    model_config = ConfigDict(extra="forbid")

    rule_ids: list[int]
    scope: int
    priority: int
    customer_id: int | None
    region_id: int | None
    product_category: str | None
    sample_date: date
    detail: str


class HarvestDuePreviewView(BaseModel):
    """Read-only result of a due-date resolution dry run.

    Reports what the engine would resolve for a HARMAN_VADELI order with the
    given customer, products and date, without writing anything.
    """

    model_config = ConfigDict(extra="forbid")

    due_date: date
    calendar_id: int
    season_code: str
    season_name: str
    season_year: int
    mode: str
    customer_id: int
    customer_region_id: int | None
    transaction_date: date
    product_categories: list[str | None]
    rule_ids: list[int]
