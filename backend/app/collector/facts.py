"""What the extraction model must return for ONE job posting."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RoleFamily = Literal[
    "ml_engineer", "ai_engineer", "data_scientist", "data_engineer", "data_analyst",
    "mlops_engineer", "research_scientist", "software_engineer", "other",
]
Seniority = Literal["intern", "junior", "mid", "senior", "lead", "unknown"]
WorkMode = Literal["remote", "hybrid", "onsite", "unknown"]
YesNoUnknown = Literal["yes", "no", "not_mentioned"]
RemoteScope = Literal["worldwide", "region", "country_only", "not_applicable", "unknown"]
LanguageLevel = Literal["required", "preferred"]
QualityFlag = Literal["ok", "suspicious_template", "not_a_job", "duplicate_like", "too_short"]
SalaryPeriod = Literal["year", "month", "day", "hour"]


class LanguageRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: str = Field(description="Language name in English, e.g. 'German'.")
    level: LanguageLevel


class SalaryFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min: float | None = Field(description="Lowest amount stated, as a number. null if not stated.")
    max: float | None = Field(description="Highest amount stated. null if not stated.")
    currency: str | None = Field(description="ISO 4217 code, e.g. 'EUR', 'GBP', 'NGN'. null if unclear.")
    period: SalaryPeriod | None


class PostingFacts(BaseModel):
    """Facts extracted from one job posting. Use ONLY what the text states; never guess."""

    model_config = ConfigDict(extra="forbid")

    # what the role is
    role_family: RoleFamily = Field(description="Best-fitting family; 'other' for non-data/AI roles.")
    seniority: Seniority = Field(description="From title and years of experience; 'unknown' if unclear.")
    years_experience_min: int | None = Field(description="Minimum years required, e.g. 2 for '2-5 years'.")

    # where / how
    work_mode: WorkMode
    remote_scope: RemoteScope = Field(
        description="If remote: who can apply. 'worldwide', 'region' (e.g. EMEA), 'country_only', "
                    "'not_applicable' if not remote, 'unknown' if not stated."
    )
    visa_sponsorship: YesNoUnknown
    relocation_support: YesNoUnknown
    clearance_required: YesNoUnknown = Field(description="Security clearance (e.g. SC, Top Secret).")
    language_requirements: list[LanguageRequirement] = Field(
        description="Spoken languages the candidate needs. Empty list if none are mentioned."
    )

    # what it needs
    skills: list[str] = Field(
        description="Technical skills/tools named in the text, as written (e.g. 'PyTorch', 'AWS'). "
                    "No soft skills. Max 25."
    )
    salary: SalaryFacts | None = Field(description="Only if the text states pay. null otherwise.")

    # trust
    quality_flag: QualityFlag = Field(
        description="'suspicious_template' for generic copy-paste postings with no company-specific "
                    "detail; 'not_a_job' for ads/training offers; 'too_short' if almost no content."
    )
    confidence: float = Field(description="0.0-1.0: how confident you are in these facts overall.")
    evidence: str = Field(description="One short quote from the posting supporting seniority/work mode.")