"""Browser tabs and desktop shortcuts must load icons before login."""
import http.client
import json
import struct
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from backend.config import ServerConfig
from backend.server import make_server

WEB = Path(__file__).resolve().parents[1]
PUBLIC = WEB / 'public'


class HeadLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.meta = []

    def handle_starttag(self, tag, attrs):
        if tag == 'link':
            self.links.append(dict(attrs))
        elif tag == 'meta':
            self.meta.append(dict(attrs))


class AppIconTests(unittest.TestCase):
    def test_browser_and_apple_icons_are_explicitly_linked(self):
        head = HeadLinks()
        head.feed((WEB / 'index.html').read_text(encoding='utf-8'))
        by_rel = {}
        for link in head.links:
            by_rel.setdefault(link.get('rel'), []).append(link)
        self.assertIn('/favicon.ico', [item['href'].split('?')[0] for item in by_rel['icon']])
        self.assertIn('/favicon.svg', [item['href'].split('?')[0] for item in by_rel['icon']])
        self.assertEqual(by_rel['apple-touch-icon'][0]['sizes'], '180x180')
        self.assertEqual(by_rel['manifest'][0]['href'].split('?')[0], '/manifest.webmanifest')
        self.assertIn({'name': 'theme-color', 'content': '#ffffff'}, head.meta)

    def test_desktop_manifest_uses_large_regular_and_maskable_icons(self):
        manifest = json.loads((PUBLIC / 'manifest.webmanifest').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], 'DataBack')
        self.assertEqual(manifest['start_url'], '/')
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['background_color'], '#ffffff')
        self.assertEqual(manifest['theme_color'], '#ffffff')
        for size in ('192x192', '512x512'):
            self.assertTrue(any(icon['sizes'] == size and icon.get('purpose', 'any') == 'any'
                                for icon in manifest['icons']))
        self.assertTrue(any(icon.get('purpose') == 'maskable' for icon in manifest['icons']))
        for icon in manifest['icons']:
            data = (PUBLIC / icon['src'].lstrip('/')).read_bytes()
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
            width, height = struct.unpack('>II', data[16:24])
            self.assertEqual(f'{width}x{height}', icon['sizes'])

    def test_raster_icons_are_opaque_and_svg_covers_the_entire_background(self):
        pngs = list(PUBLIC.glob('*.png'))
        self.assertGreaterEqual(len(pngs), 5)
        for path in pngs:
            data = path.read_bytes()
            # RGB pixels without a transparency chunk cannot have transparent corners.
            self.assertEqual(data[25], 2, path.name)
            self.assertNotIn(b'tRNS', data, path.name)
        svg = ET.parse(PUBLIC / 'favicon.svg').getroot()
        background = svg[0]
        self.assertEqual(background.attrib.get('fill'), '#ffffff')
        self.assertEqual(background.attrib.get('width'), '32')
        self.assertEqual(background.attrib.get('height'), '32')
        self.assertEqual(background.attrib.get('rx', '0'), '0')
        self.assertEqual((PUBLIC / 'favicon.ico').read_bytes()[:6], b'\x00\x00\x01\x00\x03\x00')

    def test_all_declared_icons_load_without_login_on_protected_deployments(self):
        config = ServerConfig(host='127.0.0.1', port=0, public_origin='https://backup.example.test',
                              allowed_hosts=frozenset({'backup.example.test'}),
                              admin_password='fixture-admin-A12x!z9',
                              session_secret='fixture-session-strong-13xyZA!8902bc',
                              privacy_password='fixture-private')
        with tempfile.TemporaryDirectory() as folder:
            server = make_server(port=0, db_path=Path(folder) / 'ledger.sqlite3', static_dir=PUBLIC, config=config)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                files = ['favicon.ico', 'favicon.svg', 'manifest.webmanifest'] + [p.name for p in PUBLIC.glob('*.png')]
                for name in files:
                    with self.subTest(file=name):
                        connection.request('GET', '/' + name, headers={'Host': 'backup.example.test'})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read(), (PUBLIC / name).read_bytes())
                        if name.endswith('.png'):
                            self.assertEqual(response.getheader('Content-Type'), 'image/png')
                connection.request('GET', '/api/state', headers={'Host': 'backup.example.test'})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 401)
            finally:
                connection.close()
                server.shutdown()
                worker.join()
                server.server_close()
