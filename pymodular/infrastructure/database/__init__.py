"""Tầng database: hợp đồng chung, các backend cụ thể, và Repository dùng chung.

Import từ đây:  from pymodular.infrastructure.database import Repository
"""

from pymodular.infrastructure.database.repository import Database, Repository

__all__ = ["Database", "Repository"]
