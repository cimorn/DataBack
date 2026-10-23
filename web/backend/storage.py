"""Explicit backend selection: local SQLite by default, Mongo when configured."""

import os
from pathlib import Path

from .store import Store, StoreError


class SQLiteStore(Store):
    backend = "sqlite"

    def readiness(self):
        self.state()
        return True

    def close(self):
        # SQLite opens and closes each connection inside its operation.
        pass


def create_store(db_path=None, *, mongo_uri=None, mongo_database="databack"):
    if mongo_uri is None:
        path = db_path if db_path is not None else Path(os.environ.get("DATABACK_DATA_DIR", Path(__file__).resolve().parents[2] / "data")) / "ledger.sqlite3"
        return SQLiteStore(path)
    try:
        from .mongo_store import MongoStore
        return MongoStore(mongo_uri, mongo_database)
    except StoreError:
        raise
    except Exception:
        # Includes unavailable driver and malformed URI; never switch backend.
        raise StoreError("MongoDB 存储暂不可用，请检查部署配置和副本集状态。", 503) from None
