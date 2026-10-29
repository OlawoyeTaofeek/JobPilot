"""One row per (posting, skill): the table behind 'top skills for a role' questions."""

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from .posting import Posting


class PostingSkill(Base):
    __tablename__ = "posting_skills"
    __table_args__ = (UniqueConstraint("posting_id", "skill"),)  # a skill counts once per posting

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    posting_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("postings.id", ondelete="CASCADE"), index=True
    )
    skill: Mapped[str] = mapped_column(String(80), index=True)       # normalized: "tensorflow"
    raw_skill: Mapped[str] = mapped_column(String(120))              # as written: "Tensor Flow"

    posting: Mapped["Posting"] = relationship(back_populates="skills")  # noqa: F821