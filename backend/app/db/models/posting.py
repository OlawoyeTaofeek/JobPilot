"""Collected postings + posting_skills: the demand-study dataset.
"""
from sqlalchemy import Integer, BigInteger, String, Text, Boolean, Numeric, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, func, Float
from app.db.base import Base, TimestampMixin
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from decimal import Decimal
# from .posting_skill import PostingSkill

class Posting(TimestampMixin, Base):
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("source", "source_job_id"),
        CheckConstraint("salary_min IS NULL OR salary_max is NULL OR salary_min <= salary_max", name="salary_range")
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # identity / dedupe
    source: Mapped[str] = mapped_column(String(30))
    source_job_id: Mapped[str] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    # what the job is
    title: Mapped[str] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(255), index=True)
    company_website: Mapped[str | None] = mapped_column(String(500))
    publisher: Mapped[str | None] = mapped_column(String(100), index=True)
    description: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(String(2048))
    employment_type: Mapped[str | None] = mapped_column(String(50))

    # where
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    location_text: Mapped[str | None] = mapped_column(String(255))
    is_remote_source: Mapped[bool | None] = mapped_column(Boolean)

    # pay
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    salary_period: Mapped[str | None] = mapped_column(String(20))

    # when / how we got it
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    search_query: Mapped[str] = mapped_column(String(255))
    raw: Mapped[dict] = mapped_column(JSONB)

    # ---- provenance + extra source fields (filled by normalizers when the source has them)
    provider: Mapped[str | None] = mapped_column(String(30))          # rapidapi, anyapi, direct...
    external_apply_url: Mapped[str | None] = mapped_column(String(2048))
    applicants: Mapped[int | None] = mapped_column(Integer)

    # ---- extracted facts (Milestone 2) - NULL until the extractor has run
    role_family: Mapped[str | None] = mapped_column(String(30), index=True)
    seniority: Mapped[str | None] = mapped_column(String(20), index=True)
    years_experience_min: Mapped[int | None] = mapped_column(Integer)
    work_mode: Mapped[str | None] = mapped_column(String(20), index=True)
    remote_scope: Mapped[str | None] = mapped_column(String(20))
    visa_sponsorship: Mapped[str | None] = mapped_column(String(15), index=True)
    relocation_support: Mapped[str | None] = mapped_column(String(15))
    clearance_required: Mapped[str | None] = mapped_column(String(15))
    required_languages: Mapped[list[str] | None] = mapped_column(ARRAY(String(40)))
    preferred_languages: Mapped[list[str] | None] = mapped_column(ARRAY(String(40)))
    quality_flag: Mapped[str | None] = mapped_column(String(30), index=True)

    # ---- extraction bookkeeping
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    extraction_version: Mapped[str | None] = mapped_column(String(20))
    extraction_model: Mapped[str | None] = mapped_column(String(60))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    extraction_evidence: Mapped[str | None] = mapped_column(Text)
    extraction_error: Mapped[str | None] = mapped_column(Text)

    skills: Mapped[list["PostingSkill"]] = relationship(
        back_populates="posting", cascade="all, delete-orphan", passive_deletes=True
    )