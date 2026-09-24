"""
Database package for MAX Smart City housing platform.
Provides lightweight, zero-dependency SQLite persistence with WAL mode and thread-safe connection management.
"""

from app.db.database import Database, get_db, db

__all__ = ["Database", "get_db", "db"]
