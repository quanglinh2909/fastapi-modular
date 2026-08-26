"""Tầng database: hợp đồng chung, các backend cụ thể, và Repository dùng chung.

Import từ đây:  from fastapi_modular.infrastructure.database import Repository
"""

from fastapi_modular.infrastructure.database.base import Entity, Reference, reference
from fastapi_modular.infrastructure.database.query import (
    Aggregate,
    F,
    Query,
    and_,
    avg,
    between,
    count,
    ilike,
    in_,
    is_not_null,
    is_null,
    like,
    max_,
    min_,
    not_,
    not_in,
    or_,
    sum_,
)
from fastapi_modular.infrastructure.database.repository import Database, Repository

__all__ = [
    "Aggregate",
    "Database",
    "Entity",
    "F",
    "Query",
    "Reference",
    "Repository",
    "and_",
    "avg",
    "between",
    "count",
    "ilike",
    "in_",
    "is_not_null",
    "is_null",
    "like",
    "max_",
    "min_",
    "not_",
    "not_in",
    "or_",
    "reference",
    "sum_",
]
