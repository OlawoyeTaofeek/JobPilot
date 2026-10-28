"""Async engine + session factory."""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession

from app.config import get_settings

engine = create_async_engine(
    get_settings().database_url, 
    pool_pre_ping=True, # Checks if a connection is alive before using it
    pool_size=10,
    max_overflow=10,
    pool_recycle=1800,
    echo=get_settings().app_env != "dev"
)
AsyncSessionLocal = async_sessionmaker(
    engine, expire_on_commit=False,
    class_=AsyncSession, autoflush=False,
    autocommit=False
)
