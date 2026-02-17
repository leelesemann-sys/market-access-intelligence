"""SQLAlchemy connection helper for Azure SQL.

Provides get_engine(), execute(), fetch_all(), fetch_df().
Uses ODBC Driver 18 with connection pooling.
"""

import urllib
import logging

import pandas as pd
from sqlalchemy import create_engine, text

from config import SQL_SERVER, SQL_DATABASE, SQL_USER, SQL_PASSWORD

log = logging.getLogger(__name__)

_engine = None


def get_engine():
    """Create or return cached SQLAlchemy Engine."""
    global _engine
    if _engine is None:
        odbc_str = (
            f"DRIVER={{ODBC Driver 18 for SQL Server}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USER};"
            f"PWD={SQL_PASSWORD};"
            f"Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30"
        )
        params = urllib.parse.quote_plus(odbc_str)
        _engine = create_engine(
            f"mssql+pyodbc:///?odbc_connect={params}",
            pool_pre_ping=True,
            pool_recycle=300,
        )
        log.info("SQLAlchemy engine created for %s/%s", SQL_SERVER, SQL_DATABASE)
    return _engine


def execute(query, params=None):
    """Execute DML/DDL (INSERT, UPDATE, CREATE). Auto-commits."""
    engine = get_engine()
    with engine.begin() as conn:
        return conn.execute(text(query), params or {})


def fetch_all(query, params=None):
    """Execute SELECT, return list of Row objects."""
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        return result.fetchall()


def fetch_df(query, params=None):
    """Execute SELECT, return pandas DataFrame."""
    engine = get_engine()
    return pd.read_sql(text(query), engine, params=params)
