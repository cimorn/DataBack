"""Portable container startup must preserve the selected storage backend."""
import http.client
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.config import ServerConfig
from backend.server import make_server


class ContainerRuntimeTests(unittest.TestCase):
    def test_container_without_mongo_uses_sqlite_and_partial_credentials_fail(self):
        from runtime.configuration import mongo_uri_from_env
        self.assertIsNone(mongo_uri_from_env({}))
        for values in ({'MONGODB_USERNAME': 'app'}, {'MONGODB_PASSWORD': 'private-fixture'}):
            with self.assertRaises(ValueError) as error:
                mongo_uri_from_env(values)
            self.assertNotIn('private-fixture', str(error.exception))
        self.assertEqual(mongo_uri_from_env({'MONGODB_URI': 'mongodb://fixture/'}), 'mongodb://fixture/')

    def test_container_preserves_existing_mongo_connection(self):
        from runtime.configuration import mongo_uri_from_env
        self.assertEqual(mongo_uri_from_env({'MONGODB_USERNAME': 'app', 'MONGODB_PASSWORD': 'a@b:c'}),
                         'mongodb://app:a%40b%3Ac@databack-mongodb:27017/databack?authSource=databack&replicaSet=databack')

    def test_loopback_http_supports_login_and_privacy_but_remote_http_is_rejected(self):
        values = dict(host='0.0.0.0', port=0, public_origin='http://127.0.0.1:8767',
                      allowed_hosts=frozenset({'127.0.0.1:8767'}),
                      admin_password='fixture-admin-A12x!z9',
                      session_secret='fixture-session-strong-13xyZA!8902bc', privacy_password='fixture-privacy')
        config = ServerConfig(**values)
        self.assertFalse(config.secure_cookies)
        for origin in ('http://example.test', 'http://192.0.2.1', 'http://localhost.evil.test'):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                ServerConfig(**{**values, 'public_origin': origin,
                                'allowed_hosts': frozenset({origin.split('://')[1]})})
        with tempfile.TemporaryDirectory() as folder:
            server = make_server(port=0, db_path=Path(folder) / 'ledger.sqlite3', config=config)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                headers = {'Host': '127.0.0.1:8767', 'Origin': config.public_origin,
                           'Content-Type': 'application/json'}
                connection.request('POST', '/api/login', '{"password":"fixture-admin-A12x!z9"}', headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                cookies = response.getheaders()
                session = next(value for name, value in cookies if name.lower() == 'set-cookie' and value.startswith('databack_session='))
                self.assertIn('HttpOnly', session)
                self.assertNotIn('Secure', session)
                headers['Cookie'] = session.split(';')[0]
                connection.request('POST', '/api/privacy/unlock', '{"password":"fixture-privacy"}', headers)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                self.assertNotIn('Secure', response.getheader('Set-Cookie'))
            finally:
                connection.close()
                server.shutdown()
                worker.join()
                server.server_close()

    def test_persistent_directory_is_independent_of_source_layout(self):
        from backend.storage import create_store
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {'DATABACK_DATA_DIR': folder}):
            store = create_store()
            self.assertEqual(store.db_path, Path(folder).resolve() / 'ledger.sqlite3')
            store.close()


if __name__ == '__main__':
    unittest.main()
