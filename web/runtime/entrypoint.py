"""Select SQLite or an explicitly configured MongoDB connection."""
import os
import sys
from backend.server import main
from .configuration import mongo_uri_from_env
def run():
    try:
        uri = mongo_uri_from_env()
        if uri is not None:
            os.environ['MONGODB_URI'] = uri
    except ValueError:
        print('Database configuration is invalid.', file=sys.stderr)
        return 1
    return main()
if __name__ == '__main__':
    raise SystemExit(run())
