"""Initialize the disposable replica set used by GitHub Actions tests."""
import time

from pymongo import MongoClient
from pymongo.errors import PyMongoError


def main():
    client = MongoClient('mongodb://127.0.0.1:27017/?directConnection=true', serverSelectionTimeoutMS=1000)
    for _ in range(60):
        try:
            hello = client.admin.command('hello')
            if not hello.get('setName'):
                client.admin.command('replSetInitiate', {'_id': 'databack_ci', 'members': [{'_id': 0, 'host': '127.0.0.1:27017'}]})
            elif hello.get('isWritablePrimary'):
                print('Isolated CI MongoDB replica set is ready.')
                return 0
        except PyMongoError:
            pass
        time.sleep(1)
    raise RuntimeError('Isolated CI MongoDB did not become primary.')


if __name__ == '__main__':
    raise SystemExit(main())
