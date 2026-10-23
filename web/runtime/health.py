"""Check the application's selected storage backend."""
import http.client
import json
import os
def main():
    port = int(os.environ.get('DATABACK_PORT', '8767'))
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
    try:
        connection.request('GET', '/api/health', headers={'Host': f'127.0.0.1:{port}'})
        response = connection.getresponse()
        health = json.loads(response.read(4096))
        return 0 if response.status == 200 and health.get('status') == 'ok' and health.get('product') == 'databack' and health.get('storage') in ('sqlite', 'mongodb') else 1
    except Exception:
        return 1
    finally:
        connection.close()
if __name__ == '__main__':
    raise SystemExit(main())
