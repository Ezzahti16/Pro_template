# Part of dbc_db_access_guard. See LICENSE file for full copyright details.

from unittest.mock import patch

from odoo.tests import common, tagged

from ..guard import config_store as store
from ..guard import middleware

SECRET = '/database/technique-acces'


def _config(**overrides):
    return store.sanitize(dict({
        'enabled': True,
        'secret_path': SECRET,
        'block_selector': True,
        'block_db_rpc': True,
        'allowed_ips': (),
    }, **overrides))


@tagged('post_install', '-at_install')
class TestGuardValues(common.TransactionCase):
    """Validation and normalisation of the guard settings."""

    def test_normalize_secret_path(self):
        self.assertEqual(store.normalize_secret_path(SECRET), SECRET)
        self.assertEqual(store.normalize_secret_path('  /database/technique-acces/  '), SECRET)
        self.assertEqual(store.normalize_secret_path('database//technique-acces'), SECRET)
        self.assertEqual(store.normalize_secret_path('/dbmgr'), '/dbmgr')

        for invalid in (
            '',
            '  ',
            '/',
            '/abc',                      # too short
            '/db mgr',                   # space
            '/web/database/manager',     # the very prefix we protect
            '/web/secret',               # reserved first segment
            '/odoo/secret',              # reserved first segment
            '/secret/static/x',          # static segment
            '/secret/../etc',            # traversal
            '/secret/<script>',          # invalid characters
        ):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                store.normalize_secret_path(invalid)

    def test_normalize_allowed_ips(self):
        self.assertEqual(store.normalize_allowed_ips(''), ())
        self.assertEqual(
            store.normalize_allowed_ips('10.0.0.5, 192.168.1.0/24;10.0.0.5'),
            ('10.0.0.5/32', '192.168.1.0/24'),
        )
        with self.assertRaises(ValueError):
            store.normalize_allowed_ips('not-an-ip')

    def test_sanitize_falls_back_on_defaults(self):
        self.assertEqual(store.sanitize(None), store.sanitize({}))
        self.assertEqual(
            store.sanitize({'secret_path': '/web/database/manager'})['secret_path'],
            store.DEFAULT_SECRET_PATH,
        )
        self.assertTrue(store.sanitize({})['enabled'], "the guard is secure by default")

    def test_settings_roundtrip(self):
        """Saving the settings updates both the parameters and the store."""
        before = dict(store.read_config())
        self.addCleanup(store.write_config, before)

        settings = self.env['res.config.settings'].create({
            'dbc_dbguard_enabled': True,
            'dbc_dbguard_secret_path': 'my-secret//db-console/',
            'dbc_dbguard_block_selector': False,
            'dbc_dbguard_block_db_rpc': True,
            'dbc_dbguard_allowed_ips': '10.0.0.5',
        })
        settings.execute()

        parameters = self.env['ir.config_parameter'].sudo()
        self.assertEqual(
            parameters.get_param(store.PARAMS['secret_path']), '/my-secret/db-console',
        )
        guard_config = store.read_config()
        self.assertEqual(guard_config['secret_path'], '/my-secret/db-console')
        self.assertFalse(guard_config['block_selector'])
        self.assertEqual(guard_config['allowed_ips'], ('10.0.0.5/32',))
        self.assertEqual(
            self.env['res.config.settings'].create({}).dbc_dbguard_secret_path,
            '/my-secret/db-console',
            "the saved path is what the settings screen shows",
        )

    def test_settings_refuse_invalid_values(self):
        from odoo.exceptions import ValidationError
        for values in (
            {'dbc_dbguard_secret_path': '/web/anything'},
            {'dbc_dbguard_secret_path': 'x'},
            {'dbc_dbguard_allowed_ips': '300.1.1.1'},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                settings = self.env['res.config.settings'].create(
                    dict({'dbc_dbguard_secret_path': SECRET}, **values)
                )
                settings.execute()


@tagged('post_install', '-at_install')
class TestGuardPaths(common.TransactionCase):
    """Pure path matching, independent from any HTTP request."""

    def test_match_secret_path(self):
        self.assertEqual(middleware.match_secret_path(SECRET, SECRET), '')
        self.assertEqual(middleware.match_secret_path(SECRET + '/', SECRET), '')
        self.assertEqual(middleware.match_secret_path(SECRET + '/manager', SECRET), 'manager')
        self.assertEqual(middleware.match_secret_path(SECRET + '/selector', SECRET), 'selector')
        self.assertIsNone(middleware.match_secret_path('/database/technique', SECRET))
        self.assertIsNone(middleware.match_secret_path('/web/database/manager', SECRET))
        self.assertIsNone(middleware.match_secret_path('/', SECRET))

    def test_blocked_paths(self):
        guard_config = _config()
        for path in (
            '/web/database',
            '/web/database/',
            '/web/database/manager',
            '/web/database/manager/',
            '/web//database//manager',
            '/web/database/selector',
            '/web/database/create',
            '/web/database/drop',
            '/web/database/backup',
            '/web/database/restore',
            '/web/database/duplicate',
            '/web/database/change_password',
            '/web/database/list',
        ):
            with self.subTest(path=path):
                self.assertTrue(middleware.is_blocked_path(path, guard_config))

        for path in ('/', '/odoo', '/web/login', '/web/static/img/favicon.ico',
                     '/web/databases', SECRET, SECRET + '/manager'):
            with self.subTest(path=path):
                self.assertFalse(middleware.is_blocked_path(path, guard_config))

    def test_public_selector_option(self):
        guard_config = _config(block_selector=False)
        self.assertFalse(middleware.is_blocked_path('/web/database/selector', guard_config))
        self.assertTrue(middleware.is_blocked_path('/web/database/manager', guard_config))

    def test_ip_allow_list(self):
        self.assertTrue(middleware.ip_allowed({'REMOTE_ADDR': '1.2.3.4'}, _config()))
        restricted = _config(allowed_ips='10.0.0.0/24')
        self.assertTrue(middleware.ip_allowed({'REMOTE_ADDR': '10.0.0.7'}, restricted))
        self.assertFalse(middleware.ip_allowed({'REMOTE_ADDR': '10.0.1.7'}, restricted))
        self.assertFalse(middleware.ip_allowed({'REMOTE_ADDR': 'not-an-ip'}, restricted))
        self.assertFalse(middleware.ip_allowed(None, restricted))


@tagged('post_install', '-at_install')
class TestGuardHttp(common.HttpCase):
    """End to end behaviour through the real WSGI stack."""

    def _patch_config(self, **overrides):
        guard_config = _config(**overrides)
        patcher = patch.object(store, 'read_config', return_value=guard_config)
        patcher.start()
        self.addCleanup(patcher.stop)
        return guard_config

    def test_manager_and_selector_are_hidden(self):
        self._patch_config()
        for path in ('/web/database/manager', '/web/database/selector',
                     '/web/database/list', '/web/database/manager/'):
            with self.subTest(path=path):
                self.assertEqual(self.url_open(path).status_code, 404)

    def test_secret_path_serves_the_manager(self):
        self._patch_config()
        response = self.url_open(SECRET)
        self.assertEqual(response.status_code, 200)
        self.assertIn('o_database_list', response.text)
        self.assertNotIn('/web/database', response.text,
                         "every manager URL must be rewritten to the secret path")
        self.assertIn(SECRET + '/create', response.text)

        self.assertEqual(self.url_open(SECRET + '/manager').status_code, 200)
        self.assertEqual(self.url_open(SECRET + '/selector').status_code, 200)
        self.assertEqual(self.url_open(SECRET + '/unknown').status_code, 404)

    def test_secret_path_can_be_changed(self):
        self._patch_config(secret_path='/other-secret')
        self.assertEqual(self.url_open('/other-secret').status_code, 200)
        self.assertEqual(self.url_open(SECRET).status_code, 404)

    def test_selector_can_stay_public(self):
        self._patch_config(block_selector=False)
        self.assertEqual(self.url_open('/web/database/selector').status_code, 200)
        self.assertEqual(self.url_open('/web/database/manager').status_code, 404)

    def test_ip_restriction_hides_the_secret_path(self):
        self._patch_config(allowed_ips='10.123.45.0/24')
        self.assertEqual(self.url_open(SECRET).status_code, 404)

    def test_disabled_guard_restores_the_stock_urls(self):
        self._patch_config(enabled=False)
        self.assertEqual(self.url_open('/web/database/manager').status_code, 200)
        self.assertEqual(self.url_open(SECRET).status_code, 404)

    def test_db_rpc_service_is_refused(self):
        self._patch_config()
        response = self.url_open(
            '/jsonrpc',
            data='{"jsonrpc": "2.0", "method": "call", "id": 1, "params":'
                 ' {"service": "db", "method": "list", "args": []}}',
            headers={'Content-Type': 'application/json'},
        )
        self.assertNotIn(self.env.cr.dbname, response.text,
                         "the database names must not leak over RPC")

    def test_other_routes_are_untouched(self):
        self._patch_config()
        self.assertEqual(self.url_open('/web/login').status_code, 200)
        self.assertEqual(
            self.url_open('/web/static/img/favicon.ico').status_code, 200,
        )
