"""Import every model here so Base.metadata knows about it (Alembic autogenerate
only sees models that have been imported). Add a line each time you write a new model."""

from app.db.models.collector_runs import CollectorRun
from app.db.models.posting import Posting
from app.db.models.posting_skill import PostingSkill
from app.db.models.user import OAuthCredential, User

__all__ = [
    "CollectorRun", "OAuthCredential", "Posting", "PostingSkill", "User"
]

# Add as you build them, e.g.:
# from app.db.models.posting import Posting