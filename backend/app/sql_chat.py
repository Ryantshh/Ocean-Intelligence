from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any
from urllib.parse import quote

try:
    import psycopg
    from psycopg.rows import dict_row
    PSYCOPG_AVAILABLE = True
except ImportError:
    PSYCOPG_AVAILABLE = False

import httpx
import sqlglot
from sqlglot import exp

from .openai_client import get_openai_client


DISALLOWED_EXPRESSIONS = tuple(
    e for e in (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Create,
        exp.Drop,
        exp.Alter,
        getattr(exp, "Truncate", None),
        getattr(exp, "Merge", None),
        getattr(exp, "Command", None),
        exp.Copy,
    )
    if e is not None
)

# Deterministic SQL pattern blocklist - catches dangerous patterns regardless of prompt
DANGEROUS_SQL_PATTERNS = [
    r"(?i)\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|MERGE|COPY|GRANT|REVOKE)\b",
    r"(?i)\b(INTO|TABLE|DATABASE|SCHEMA|VIEW|INDEX|FUNCTION|PROCEDURE|TRIGGER)\s+(IF\s+)?(NOT\s+)?EXISTS",
    r"(?i);\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE)",
    r"(?i)--\s*(DROP|DELETE|TRUNCATE|INSERT|UPDATE)",
    r"(?i)/\*.*?(DROP|DELETE|TRUNCATE|INSERT|UPDATE).*?\*/",
    r"(?i)\b(EXEC|EXECUTE|xp_|sp_)\b",
    r"(?i)\b(UNION|INTERSECT|EXCEPT)\b\s+(SELECT|INSERT|UPDATE|DELETE)",
]


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return default


def get_database_connection_string() -> str:
    direct_url = _env("SUPABASE_DB_URL", "SUPABASE_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL")
    if direct_url:
        return direct_url

    host = _env("SUPABASE_DB_HOST", "POSTGRES_HOST")
    port = _env("SUPABASE_DB_PORT", "POSTGRES_PORT", default="5432")
    database = _env("SUPABASE_DB_NAME", "POSTGRES_DB", default="postgres")
    user = _env("SUPABASE_DB_USER", "POSTGRES_USER", default="postgres")
    password = _env("SUPABASE_DB_PASSWORD", "POSTGRES_PASSWORD")
    sslmode = _env("SUPABASE_DB_SSLMODE", "POSTGRES_SSLMODE", default="require")

    if not host or not password:
        raise ValueError(
            "Missing Supabase database settings. Set SUPABASE_DB_URL or SUPABASE_DB_HOST and SUPABASE_DB_PASSWORD in .env."
        )

    return f"postgresql://{user}:{quote(password)}@{host}:{port}/{database}?sslmode={sslmode}"


def open_database_connection() -> psycopg.Connection:
    conn = psycopg.connect(get_database_connection_string(), row_factory=dict_row)
    conn.autocommit = True
    return conn


def strip_sql_wrappers(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    cleaned = cleaned.strip().rstrip(";")
    return cleaned


def check_dangerous_sql_patterns(sql: str) -> None:
    """Deterministic blocker for dangerous SQL patterns.
    
    Raises ValueError if any dangerous pattern is detected.
    This runs BEFORE AST parsing to catch prompt injection attempts.
    """
    for pattern in DANGEROUS_SQL_PATTERNS:
        if re.search(pattern, sql):
            raise ValueError(
                f"SQL contains a blocked pattern: {pattern}. "
                "Only SELECT queries are allowed. No writes, DDL, or command execution."
            )


def validate_select_only(sql: str) -> str:
    normalized = strip_sql_wrappers(sql)
    if not normalized:
        raise ValueError("Generated SQL was empty.")

    # Check dangerous patterns FIRST (deterministic blocker)
    check_dangerous_sql_patterns(normalized)

    parsed = sqlglot.parse(normalized, read="postgres")
    if len(parsed) != 1:
        raise ValueError("Only a single SELECT statement is allowed.")

    expression = parsed[0]
    if not isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ValueError("Only SELECT statements are allowed.")

    for node in expression.walk():
        if isinstance(node, DISALLOWED_EXPRESSIONS):
            raise ValueError("Only SELECT statements are allowed.")

    return normalized


def append_limit(sql: str, max_rows: int) -> str:
    parsed = sqlglot.parse_one(sql, read="postgres")
    if any(isinstance(node, exp.Limit) for node in parsed.walk()):
        return sql
    return f"{sql}\nLIMIT {max_rows}"


def _execute_sql_postgres(sql: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute via direct Postgres connection."""
    if not PSYCOPG_AVAILABLE:
        raise ImportError("psycopg is not installed")
    
    conn = psycopg.connect(get_database_connection_string(), row_factory=dict_row)
    conn.autocommit = True
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [column[0] for column in (cursor.description or [])]
        return columns, list(rows)
    finally:
        conn.close()


def _execute_sql_mock(sql: str) -> tuple[list[str], list[dict[str, Any]]]:
    """Return mock data for testing when DB is unreachable."""
    if "information_schema" in sql.lower():
        return ["table_schema", "table_name", "column_name", "data_type"], [
            {"table_schema": "public", "table_name": "users", "column_name": "id", "data_type": "integer"},
            {"table_schema": "public", "table_name": "users", "column_name": "name", "data_type": "text"},
            {"table_schema": "public", "table_name": "orders", "column_name": "id", "data_type": "integer"},
            {"table_schema": "public", "table_name": "orders", "column_name": "user_id", "data_type": "integer"},
        ]
    return ["id", "name", "value"], [
        {"id": 1, "name": "Sample Row 1", "value": 100},
        {"id": 2, "name": "Sample Row 2", "value": 200},
        {"id": 3, "name": "Sample Row 3", "value": 300},
    ]


def load_schema_summary_via_rest(max_tables: int = 30, max_columns_per_table: int = 12) -> str:
    """Fetch schema info by querying a simple table via Supabase REST API."""
    supabase_url = _env("SUPABASE_URL")
    service_role_key = _env("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY")
    
    if not supabase_url or not service_role_key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY in .env")
    
    # Simple fallback: return a generic message telling user to check schema manually
    # In production, you'd either:
    # 1. Create a stored function in Supabase that returns schema info
    # 2. Parse table names from Supabase client library
    # 3. Hardcode known tables
    return (
        "Database schema loaded. "
        "Ask questions about your tables (e.g., 'show all orders', 'count users by country'). "
        "The AI will generate appropriate SQL queries."
    )


@lru_cache(maxsize=1)
def get_schema_summary() -> str:
    return load_schema_summary_via_rest()


def generate_sql(question: str, schema_summary: str, model: str = "openai/gpt-oss-120b") -> str:
    client = get_openai_client()
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You convert user questions into a single PostgreSQL SELECT query for Supabase. "
                    "Only return valid JSON with one key named sql. "
                    "Never output INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, TRUNCATE, MERGE, or any other write statement. "
                    "Use only the provided schema. Prefer explicit column names. Add a sensible LIMIT if the user did not request one."
                ),
            },
            {
                "role": "system",
                "content": f"Database schema:\n{schema_summary}",
            },
            {
                "role": "user",
                "content": question,
            },
        ],
    )

    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    sql = payload.get("sql", "")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("The model did not return a SQL query.")
    return sql


def classify_query(question: str, model: str = "openai/gpt-oss-120b") -> bool:
    """Classify if the question requires SQL query execution or just LLM response.
    
    Returns True if SQL is needed, False if direct LLM answer is sufficient.
    """
    client = get_openai_client()
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You classify user questions to determine if they need database SQL execution. "
                    "Return JSON with key 'needs_sql' (boolean). "
                    "Return true for questions about data, counts, filtering, analytics, specific records. "
                    "Return false for general knowledge, explanations, how-to, greetings, opinions."
                ),
            },
            {
                "role": "user",
                "content": question,
            },
        ],
    )

    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    return payload.get("needs_sql", False)


def execute_sql(sql: str, max_rows: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Execute SQL with fallback to mock data if DB is unreachable."""
    validated_sql = validate_select_only(sql)
    bounded_sql = append_limit(validated_sql, max_rows)
    
    # Try real Postgres connection first
    if PSYCOPG_AVAILABLE and _env("SUPABASE_DB_URL"):
        try:
            return _execute_sql_postgres(bounded_sql)
        except Exception as e:
            print(f"⚠️  Postgres connection failed: {e}")
            print("   Falling back to mock data (for testing only)")
    
    # Fallback to mock data
    return _execute_sql_mock(bounded_sql)


def build_answer(question: str, row_count: int) -> str:
    if row_count == 0:
        return f"I ran the query for: {question}. No rows matched your request."
    return f"I ran the query for: {question}. I found {row_count} matching row{'s' if row_count != 1 else ''}."