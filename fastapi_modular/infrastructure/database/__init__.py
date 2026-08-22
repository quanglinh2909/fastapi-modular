"""Tầng database: hợp đồng chung, các backend cụ thể, và Repository dùng chung.

Import từ đây:  from fastapi_modular.infrastructure.database import Repository
"""

from fastapi_modular.infrastructure.database.repository import Database, Repository

__all__ = ["Database", "Repository"]
