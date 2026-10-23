"""Publishing must not introduce a shared privacy credential."""

import unittest

from backend.config import ConfigError, ServerConfig


class PublicConfigurationTests(unittest.TestCase):
    def test_network_environment_requires_explicit_privacy_password(self):
        values = {
            'DATABACK_HOST': '0.0.0.0',
            'DATABACK_PUBLIC_ORIGIN': 'https://backup.example.test',
            'DATABACK_ALLOWED_HOSTS': 'backup.example.test',
            'DATABACK_ADMIN_PASSWORD': 'fixture-admin-strong!k29A',
            'DATABACK_SESSION_SECRET': 'fixture-session-secret-with-diverse!4A290bc',
        }
        with self.assertRaisesRegex(ConfigError, 'DATABACK_PRIVACY_PASSWORD'):
            ServerConfig.from_env(values)
        values['DATABACK_PRIVACY_PASSWORD'] = 'fixture-privacy'
        configured = ServerConfig.from_env(values)
        self.assertEqual(configured.privacy_password, 'fixture-privacy')
        self.assertNotIn(values['DATABACK_PRIVACY_PASSWORD'], repr(configured))

    def test_direct_local_instances_never_share_a_default_privacy_password(self):
        self.assertNotEqual(ServerConfig().privacy_password, ServerConfig().privacy_password)


if __name__ == '__main__':
    unittest.main()
