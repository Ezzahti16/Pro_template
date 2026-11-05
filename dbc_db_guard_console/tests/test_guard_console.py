# Part of dbc_db_guard_console. See LICENSE file for full copyright details.

import json
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tests import common, tagged

from ..tools import guard_remote as remote
from ..tools.odoo_client import RpcCallError, RpcUnreachable

DEFAULT_REMOTE = {
    'enabled': True,
    'secret_path': '/database/technique-acces',
    'block_selector': True,
    'block_db_rpc': True,
    'allowed_ips': '',
}
STOCK = remote.STOCK_MANAGER_PATH


class FakeClient:
    """In-memory stand-in for a remote Odoo instance running the agent."""

    transport = 'json2'

    def __init__(self, config=None, installed=True, agent_version='19.0.1.0.0',
                 server_wide=True, broken_http=False, fail_apply=False,
                 unreachable_http=False):
        self.config = dict(config or DEFAULT_REMOTE)
        self.installed = installed
        self.agent_version = agent_version
        self.server_wide = server_wide
        self.broken_http = broken_http
        self.fail_apply = fail_apply
        self.unreachable_http = unreachable_http
        self.pending = {}
        self.applied = []

    # --- rpc surface used by guard_remote --------------------------------
    def probe(self):
        return {'transport': self.transport, 'version': '19.0.1.3'}

    def search_read(self, model, domain, fields, limit=None):
        if model == 'ir.module.module':
            return [{
                'id': 1,
                'state': 'installed' if self.installed else 'uninstalled',
                'latest_version': self.agent_version,
            }]
        return []

    def create(self, model, values):
        self.pending = dict(values)
        return 42

    def read(self, model, ids, fields):
        values = {'id': ids[0]}
        for field in fields:
            if field == 'dbc_dbguard_server_wide':
                values[field] = self.server_wide
                continue
            key = next(k for k, f in remote.SETTINGS_FIELDS.items() if f == field)
            values[field] = self.config[key]
        return [values]

    def call_on(self, model, ids, method):
        if method != 'execute':
            return None
        if self.fail_apply:
            raise RpcCallError("the instance refused the write")
        for key, field in remote.SETTINGS_FIELDS.items():
            if field in self.pending:
                self.config[key] = self.pending[field]
        self.applied.append(dict(self.config))
        return True

    # --- plain http surface ----------------------------------------------
    def http_status(self, path):
        if self.unreachable_http:
            raise RpcUnreachable("connection refused")
        if self.broken_http:
            return 500
        if path == STOCK:
            return 404 if self.config['enabled'] else 200
        if self.config['enabled'] and path == self.config['secret_path']:
            return 200
        return 404


@tagged('post_install', '-at_install')
class TestGuardConsoleValues(common.TransactionCase):

    def test_normalize_secret_path(self):
        self.assertEqual(
            remote.normalize_secret_path('database//technique-acces/'),
            '/database/technique-acces',
        )
        for invalid in ('', '/', '/abc', '/web/database/manager', '/odoo/x',
                        '/a b/c', '/secret/static/x', '/json/2'):
            with self.subTest(path=invalid), self.assertRaises(ValueError):
                remote.normalize_secret_path(invalid)

    def test_normalize_base_url(self):
        self.assertEqual(
            remote.normalize_base_url(' https://client.example.com/ '),
            'https://client.example.com',
        )
        for invalid in ('', 'client.example.com', 'ftp://x.y', 'https://'):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                remote.normalize_base_url(invalid)

    def test_console_rules_match_the_agent(self):
        """The duplicated validator must stay identical to the agent's."""
        try:
            from odoo.addons.dbc_db_access_guard.guard import config_store
        except ImportError:
            self.skipTest("the agent module is not on this addons path")
        for path in ('/database/technique-acces', '/db-admin/x1', '/secret.path'):
            self.assertEqual(
                remote.normalize_secret_path(path),
                config_store.normalize_secret_path(path),
            )
        self.assertEqual(remote.DEFAULT_SECRET_PATH, config_store.DEFAULT_SECRET_PATH)

    def test_verify_config(self):
        client = FakeClient()
        ok, message = remote.verify_config(client, dict(DEFAULT_REMOTE))
        self.assertTrue(ok, message)

        ok, message = remote.verify_config(
            FakeClient(broken_http=True), dict(DEFAULT_REMOTE),
        )
        self.assertFalse(ok)
        self.assertIn('expected 200', message)

        # an IP allow-list makes the console blind: never a rollback reason
        ok, message = remote.verify_config(
            client, dict(DEFAULT_REMOTE, allowed_ips='10.0.0.0/8'),
        )
        self.assertIsNone(ok)
        self.assertIn('skipped', message)

        # deactivation
        disabled = FakeClient(config=dict(DEFAULT_REMOTE, enabled=False))
        ok, message = remote.verify_config(disabled, dict(DEFAULT_REMOTE, enabled=False))
        self.assertTrue(ok, message)


@tagged('post_install', '-at_install')
class TestGuardConsoleInstance(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.instance = cls.env['dbc.guard.instance'].create({
            'name': "Test instance",
            'base_url': 'https://client.example.com',
            'db_name': 'clientdb',
            'login': 'admin',
            'api_key': 'secret-key',
        })

    def _with_client(self, client):
        model = type(self.env['dbc.guard.instance'])
        return patch.object(model, '_client', lambda record: client)

    # ------------------------------------------------------------------
    def test_constraints(self):
        with self.assertRaises(ValidationError):
            self.instance.secret_path = '/web/database/manager'
        with self.assertRaises(ValidationError):
            self.instance.base_url = 'not-an-url'
        with self.assertRaises(ValidationError):
            self.instance.timeout = 0

    def test_urls_are_computed(self):
        self.instance.secret_path = '/db-admin/console'
        self.assertEqual(
            self.instance.manager_url, 'https://client.example.com/db-admin/console',
        )
        self.assertEqual(
            self.instance.selector_url,
            'https://client.example.com/db-admin/console/selector',
        )

    def test_first_fetch_adopts_remote_values(self):
        client = FakeClient(config=dict(DEFAULT_REMOTE, secret_path='/already/there'))
        with self._with_client(client):
            self.instance.action_fetch()
        self.assertEqual(self.instance.state, 'active')
        self.assertEqual(self.instance.remote_secret_path, '/already/there')
        self.assertEqual(self.instance.secret_path, '/already/there',
                         "the first sync adopts what the instance already serves")
        self.assertTrue(self.instance.log_ids)

    def test_apply_pushes_and_verifies(self):
        client = FakeClient()
        self.instance.secret_path = '/db-admin/console'
        with self._with_client(client):
            self.instance.action_apply()

        self.assertEqual(client.config['secret_path'], '/db-admin/console')
        self.assertTrue(client.config['enabled'])
        self.assertEqual(self.instance.state, 'active')
        self.assertEqual(self.instance.remote_secret_path, '/db-admin/console')
        self.assertEqual(
            json.loads(self.instance.applied_config)['secret_path'], '/db-admin/console',
        )
        self.assertTrue(self.instance.log_ids.filtered(
            lambda log: log.action == 'apply' and log.success))

    def test_apply_rolls_back_when_verification_fails(self):
        client = FakeClient(broken_http=True)
        before = dict(client.config)
        self.instance.secret_path = '/db-admin/console'
        with self._with_client(client):
            self.instance.action_apply()

        self.assertEqual(
            client.config, before,
            "a failed verification must restore the previous configuration",
        )
        self.assertEqual(self.instance.state, 'error')
        self.assertIn('Verification failed', self.instance.last_error)
        self.assertTrue(self.instance.log_ids.filtered(
            lambda log: log.action == 'rollback' and log.success),
            "the rollback must be traced in the history")

    def test_apply_refuses_when_the_agent_is_missing(self):
        client = FakeClient(installed=False)
        with self._with_client(client):
            self.instance.action_apply()
        self.assertEqual(self.instance.state, 'not_installed')
        self.assertEqual(client.applied, [], "nothing may be pushed without the agent")

    def test_apply_reports_a_remote_refusal(self):
        client = FakeClient(fail_apply=True)
        with self._with_client(client):
            self.instance.action_apply()
        self.assertEqual(self.instance.state, 'error')
        self.assertIn('refused', self.instance.last_error)

    def test_deactivate_restores_the_stock_urls(self):
        client = FakeClient()
        with self._with_client(client):
            self.instance.action_apply()
            self.instance.action_deactivate()
        self.assertFalse(client.config['enabled'])
        self.assertEqual(self.instance.state, 'inactive')

    def test_drift_is_detected(self):
        client = FakeClient()
        with self._with_client(client):
            self.instance.action_apply()
            self.assertEqual(self.instance.state, 'active')
            # somebody changes the instance behind the console's back
            client.config['secret_path'] = '/changed/by/hand'
            self.instance.action_fetch()
        self.assertEqual(self.instance.state, 'drift')
        self.assertIn('/changed/by/hand', self.instance.drift_message)

    def test_server_wide_warning_is_reported(self):
        client = FakeClient(server_wide=False)
        with self._with_client(client):
            self.instance.action_test_connection()
        self.assertFalse(self.instance.server_wide)
        self.assertEqual(self.instance.odoo_version, '19.0.1.3')

    def test_unreachable_instance_is_flagged(self):
        class Dead(FakeClient):
            def search_read(self, *args, **kwargs):
                raise RpcUnreachable("connection refused")

        with self._with_client(Dead()):
            self.instance.action_fetch()
        self.assertEqual(self.instance.state, 'unreachable')
        self.assertIn('connection refused', self.instance.last_error)
        self.assertTrue(self.instance.log_ids.filtered(lambda log: not log.success))

    def test_cron_survives_a_dead_instance(self):
        class Dead(FakeClient):
            def search_read(self, *args, **kwargs):
                raise RpcUnreachable("connection refused")

        self.instance.state = 'active'
        with self._with_client(Dead()):
            self.env['dbc.guard.instance']._cron_refresh_states()
        self.assertEqual(self.instance.state, 'unreachable')
