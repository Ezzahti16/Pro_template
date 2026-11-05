# Part of dbc_db_guard_console. See LICENSE file for full copyright details.

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import guard_remote as remote
from ..tools.odoo_client import (
    OdooClient,
    RpcAuthError,
    RpcError,
    RpcUnreachable,
)

_logger = logging.getLogger(__name__)

STATES = [
    ('draft', "Never synced"),
    ('inactive', "Not protected"),
    ('active', "Protected"),
    ('drift', "Out of sync"),
    ('not_installed', "Agent missing"),
    ('unreachable', "Unreachable"),
    ('error', "Error"),
]


class DbcGuardInstance(models.Model):
    _name = 'dbc.guard.instance'
    _description = "Managed Odoo Instance"
    _order = 'name'

    name = fields.Char(
        required=True, help="Label of the instance, for example 'Client X - production'.",
    )
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    note = fields.Text(string="Notes")

    # ------------------------------------------------------------------
    # connection
    # ------------------------------------------------------------------
    base_url = fields.Char(
        string="Instance URL", required=True,
        help="Public base URL of the instance, for example https://client.example.com",
    )
    db_name = fields.Char(
        string="Database", required=True,
        help="Database used to log in. The guard itself protects the whole Odoo "
             "process, so this is only the database the console connects to: one "
             "record is enough even when the instance hosts several databases.",
    )
    login = fields.Char(
        string="Login", required=True,
        help="User of that database. It must belong to the Settings group.",
    )
    api_key = fields.Char(
        string="API key", required=True, copy=False,
        groups='dbc_db_guard_console.group_guard_manager',
        help="Global scope API key of that user (Preferences > Account Security > "
             "New API Key). Required by the /json/2 transport; the /jsonrpc "
             "fallback also accepts a password here.",
    )
    transport = fields.Selection(
        [('auto', "Automatic"), ('json2', "/json/2 (Odoo 19+)"), ('jsonrpc', "/jsonrpc (legacy)")],
        default='auto', required=True,
        help="Automatic tries /json/2 with the API key first and falls back to "
             "/jsonrpc, which Odoo 19 deprecates and Odoo 22 removes.",
    )
    timeout = fields.Integer(default=20, required=True, help="Network timeout in seconds.")
    verify_ssl = fields.Boolean(
        string="Verify SSL", default=True,
        help="Uncheck only for an instance using a self-signed certificate.",
    )

    # ------------------------------------------------------------------
    # wanted configuration
    # ------------------------------------------------------------------
    guard_enabled = fields.Boolean(
        string="Protection", default=True,
        help="When applied: the stock /web/database/* URLs answer 404 and the "
             "database manager moves to the secret path. When off, applying "
             "gives the stock URLs back.",
    )
    secret_path = fields.Char(
        string="Secret path", default=remote.DEFAULT_SECRET_PATH, required=True,
        help="Base URL of the hidden database manager on that instance.",
    )
    block_selector = fields.Boolean(string="Block the selector", default=True)
    block_db_rpc = fields.Boolean(string="Block the db RPC service", default=True)
    allowed_ips = fields.Char(
        string="Restrict to IPs",
        help="Optional allow-list pushed to the instance (10.0.0.5, 192.168.1.0/24). "
             "Beware: with an allow-list, this console can no longer verify the "
             "secret URL over HTTP unless its own address is in the list.",
    )

    manager_url = fields.Char(compute='_compute_urls', string="Manager URL")
    selector_url = fields.Char(compute='_compute_urls', string="Selector URL")

    # ------------------------------------------------------------------
    # observed on the instance
    # ------------------------------------------------------------------
    state = fields.Selection(STATES, default='draft', readonly=True, copy=False, index=True)
    agent_installed = fields.Boolean(string="Agent installed", readonly=True, copy=False)
    agent_version = fields.Char(string="Agent version", readonly=True, copy=False)
    odoo_version = fields.Char(string="Odoo version", readonly=True, copy=False)
    used_transport = fields.Char(string="Transport in use", readonly=True, copy=False)
    server_wide = fields.Boolean(
        string="Loaded server-wide", readonly=True, copy=False,
        help="False means the instance misses 'dbc_db_access_guard' in the "
             "server_wide_modules of its odoo.conf: the guard then only starts "
             "once a registry is loaded, leaving the manager exposed for the "
             "first seconds after each restart.",
    )
    remote_enabled = fields.Boolean(string="Remote protection", readonly=True, copy=False)
    remote_secret_path = fields.Char(string="Remote secret path", readonly=True, copy=False)
    remote_block_selector = fields.Boolean(string="Remote blocks selector", readonly=True, copy=False)
    remote_block_db_rpc = fields.Boolean(string="Remote blocks db RPC", readonly=True, copy=False)
    remote_allowed_ips = fields.Char(string="Remote IP allow-list", readonly=True, copy=False)
    applied_config = fields.Text(
        readonly=True, copy=False,
        help="Last configuration this console applied successfully, used to detect drift.",
    )
    drift_message = fields.Text(readonly=True, copy=False)
    last_sync = fields.Datetime(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    log_ids = fields.One2many('dbc.guard.instance.log', 'instance_id', string="History")

    _unique_instance_db = models.Constraint(
        'UNIQUE (base_url, db_name)',
        "This instance URL and database are already registered.",
    )

    # ------------------------------------------------------------------
    # computes and constraints
    # ------------------------------------------------------------------
    @api.depends('base_url', 'secret_path', 'remote_secret_path', 'state')
    def _compute_urls(self):
        for instance in self:
            try:
                base_url = remote.normalize_base_url(instance.base_url)
            except ValueError:
                instance.manager_url = instance.selector_url = False
                continue
            path = instance.remote_secret_path if instance.state in (
                'active', 'drift',
            ) else instance.secret_path
            try:
                path = remote.normalize_secret_path(path)
            except ValueError:
                instance.manager_url = instance.selector_url = False
                continue
            instance.manager_url = base_url + path
            instance.selector_url = base_url + path + '/selector'

    @api.constrains('base_url')
    def _check_base_url(self):
        for instance in self:
            try:
                remote.normalize_base_url(instance.base_url)
            except ValueError as error:
                raise ValidationError(str(error)) from error

    @api.constrains('secret_path')
    def _check_secret_path(self):
        for instance in self:
            try:
                remote.normalize_secret_path(instance.secret_path)
            except ValueError as error:
                raise ValidationError(str(error)) from error

    @api.constrains('timeout')
    def _check_timeout(self):
        for instance in self:
            if not 1 <= instance.timeout <= 600:
                raise ValidationError(_("The timeout must be between 1 and 600 seconds."))

    @api.onchange('base_url')
    def _onchange_base_url(self):
        if self.base_url:
            self.base_url = self.base_url.strip().rstrip('/')

    @api.onchange('secret_path')
    def _onchange_secret_path(self):
        if self.secret_path:
            try:
                self.secret_path = remote.normalize_secret_path(self.secret_path)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _client(self):
        """Build an RPC client for this instance."""
        self.ensure_one()
        instance = self.sudo()
        return OdooClient(
            base_url=instance.base_url,
            db_name=instance.db_name,
            login=instance.login,
            secret=instance.api_key,
            transport=instance.transport,
            timeout=instance.timeout,
            verify_ssl=instance.verify_ssl,
        )

    def _wanted_config(self):
        self.ensure_one()
        return {
            'enabled': self.guard_enabled,
            'secret_path': remote.normalize_secret_path(self.secret_path),
            'block_selector': self.block_selector,
            'block_db_rpc': self.block_db_rpc,
            'allowed_ips': self.allowed_ips or '',
        }

    @staticmethod
    def _dump_config(config):
        return json.dumps(config, sort_keys=True)

    def _log(self, action, success, message):
        self.ensure_one()
        self.env['dbc.guard.instance.log'].sudo().create({
            'instance_id': self.id,
            'action': action,
            'success': success,
            'message': (message or '')[:4000],
        })

    @staticmethod
    def _notify(title, message, kind='success', sticky=False):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': message,
                'type': kind,
                'sticky': sticky,
            },
        }

    def _store_remote_state(self, info, transport=None, odoo_version=None):
        """Write what was observed and derive the state."""
        self.ensure_one()
        values = {
            'agent_installed': info['installed'],
            'agent_version': info.get('version') or '',
            'server_wide': info.get('server_wide', False),
            'last_sync': fields.Datetime.now(),
            'last_error': False,
            'drift_message': False,
        }
        if transport:
            values['used_transport'] = transport
        if odoo_version:
            values['odoo_version'] = odoo_version

        config = info.get('config')
        if not info['installed'] or config is None:
            values.update(
                state='not_installed', remote_enabled=False, remote_secret_path=False,
                remote_block_selector=False, remote_block_db_rpc=False,
                remote_allowed_ips=False,
            )
            self.write(values)
            return values['state']

        values.update(
            remote_enabled=config['enabled'],
            remote_secret_path=config['secret_path'],
            remote_block_selector=config['block_selector'],
            remote_block_db_rpc=config['block_db_rpc'],
            remote_allowed_ips=config['allowed_ips'],
        )

        if self.state == 'draft' and not self.applied_config:
            # first sync: adopt what the instance already enforces
            values.update(
                guard_enabled=config['enabled'],
                secret_path=config['secret_path'] or self.secret_path,
                block_selector=config['block_selector'],
                block_db_rpc=config['block_db_rpc'],
                allowed_ips=config['allowed_ips'] or False,
            )
            values['state'] = 'active' if config['enabled'] else 'inactive'
        elif self.applied_config and self._dump_config(config) != self.applied_config:
            values['state'] = 'drift'
            values['drift_message'] = self._describe_drift(config)
        else:
            values['state'] = 'active' if config['enabled'] else 'inactive'

        self.write(values)
        return values['state']

    def _describe_drift(self, config):
        self.ensure_one()
        try:
            applied = json.loads(self.applied_config or '{}')
        except ValueError:  # pragma: no cover
            applied = {}
        lines = [
            "%s: this console applied %r, the instance now serves %r"
            % (key, applied.get(key), value)
            for key, value in sorted(config.items())
            if applied.get(key) != value
        ]
        return '\n'.join(lines) or "The instance configuration changed."

    def _handle_rpc_error(self, action, error):
        """Record a failure without rolling back the history entry."""
        self.ensure_one()
        state = 'unreachable' if isinstance(error, (RpcUnreachable, RpcAuthError)) else 'error'
        self.write({
            'state': state,
            'last_error': str(error)[:4000],
            'last_sync': fields.Datetime.now(),
        })
        self._log(action, False, str(error))
        _logger.warning("Guard console: %s failed on %s: %s", action, self.display_name, error)
        return self._notify(
            _("%s failed", self.display_name), str(error), kind='danger', sticky=True,
        )

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def action_test_connection(self):
        self.ensure_one()
        client = self._client()
        try:
            probe = client.probe()
            info = remote.read_agent_state(client)
        except RpcError as error:
            return self._handle_rpc_error('test', error)

        self._store_remote_state(
            info, transport=client.transport, odoo_version=probe['version'],
        )
        message = _(
            "Connected over %(transport)s to Odoo %(version)s. Agent: %(agent)s.",
            transport=client.transport,
            version=probe['version'] or _("unknown"),
            agent=_("installed (%s)", info['version']) if info['installed'] else _("not installed"),
        )
        if info['installed'] and not info.get('server_wide'):
            message += _(
                "\nWarning: the agent is not in server_wide_modules on that "
                "instance, so the manager stays exposed for the first seconds "
                "after each restart."
            )
        self._log('test', True, message)
        return self._notify(
            self.display_name, message,
            kind='success' if info['installed'] else 'warning',
            sticky=not info['installed'],
        )

    def action_fetch(self):
        """Read the configuration the instance really enforces."""
        self.ensure_one()
        client = self._client()
        try:
            info = remote.read_agent_state(client)
        except RpcError as error:
            return self._handle_rpc_error('fetch', error)

        state = self._store_remote_state(info, transport=client.transport)
        if state == 'not_installed':
            message = _("The agent module is not installed on that instance.")
        elif state == 'drift':
            message = _("The instance no longer matches what this console applied:\n%s", self.drift_message)
        else:
            message = _(
                "Protection is %(status)s, database manager on %(path)s.",
                status=_("active") if self.remote_enabled else _("off"),
                path=self.remote_secret_path or '-',
            )
        self._log('fetch', state not in ('not_installed',), message)
        return self._notify(
            self.display_name, message,
            kind='success' if state in ('active', 'inactive') else 'warning',
            sticky=state in ('drift', 'not_installed'),
        )

    def action_apply(self):
        """Push the configuration, verify it over HTTP, roll back on failure."""
        self.ensure_one()
        try:
            wanted = self._wanted_config()
        except ValueError as error:
            raise UserError(str(error)) from error

        client = self._client()
        try:
            before = remote.read_agent_state(client)
        except RpcError as error:
            return self._handle_rpc_error('apply', error)

        if not before['installed']:
            self._store_remote_state(before, transport=client.transport)
            message = _(
                "The agent module 'dbc_db_access_guard' is not installed on that "
                "instance: install it there first, nothing was changed."
            )
            self._log('apply', False, message)
            return self._notify(self.display_name, message, kind='danger', sticky=True)

        try:
            remote.apply_config(client, wanted)
        except RpcError as error:
            return self._handle_rpc_error('apply', error)

        # verify what the instance now really serves
        try:
            ok, verdict = remote.verify_config(client, wanted)
        except RpcError as error:
            ok, verdict = False, str(error)

        if ok is False:
            rollback_message = self._rollback(client, before, verdict)
            return self._notify(
                _("%s: rolled back", self.display_name), rollback_message,
                kind='danger', sticky=True,
            )

        self.applied_config = self._dump_config(wanted)
        try:
            after = remote.read_agent_state(client)
            self._store_remote_state(after, transport=client.transport)
        except RpcError as error:  # pragma: no cover - applied but unreadable
            self.write({'last_error': str(error)[:4000]})

        message = _("Applied: %(verdict)s", verdict=verdict)
        self._log('apply', True, message)
        return self._notify(
            self.display_name, message,
            kind='success' if ok else 'warning', sticky=ok is None,
        )

    def _rollback(self, client, before, reason):
        """Restore the configuration the instance had before a failed apply."""
        self.ensure_one()
        previous = before['config']
        detail = _("Verification failed: %s", reason)
        try:
            remote.apply_config(client, previous)
        except RpcError as error:
            message = _(
                "%(detail)s\nThe rollback ALSO failed (%(error)s). The instance "
                "may be left with the new configuration: check it now.",
                detail=detail, error=error,
            )
            self.write({'state': 'error', 'last_error': message[:4000]})
            self._log('rollback', False, message)
            return message

        message = _(
            "%(detail)s\nThe previous configuration was restored (manager on "
            "%(path)s, protection %(status)s).",
            detail=detail, path=previous['secret_path'],
            status=_("on") if previous['enabled'] else _("off"),
        )
        self.write({'state': 'error', 'last_error': message[:4000]})
        self._log('rollback', True, message)
        try:
            self._store_remote_state(
                remote.read_agent_state(client), transport=client.transport,
            )
            self.write({'state': 'error', 'last_error': message[:4000]})
        except RpcError:  # pragma: no cover
            pass
        return message

    def action_deactivate(self):
        """Give the stock /web/database URLs back on that instance."""
        self.ensure_one()
        self.guard_enabled = False
        return self.action_apply()

    def action_open_manager(self):
        self.ensure_one()
        if not self.manager_url:
            raise UserError(_("This instance has no usable manager URL yet."))
        return {'type': 'ir.actions.act_url', 'url': self.manager_url, 'target': 'new'}

    def action_view_logs(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("History of %s", self.display_name),
            'res_model': 'dbc.guard.instance.log',
            'view_mode': 'list,form',
            'domain': [('instance_id', '=', self.id)],
            'context': {'default_instance_id': self.id},
        }

    # ------------------------------------------------------------------
    # cron
    # ------------------------------------------------------------------
    @api.model
    def _cron_refresh_states(self):
        """Re-read every instance and flag the ones that drifted."""
        instances = self.search([('state', '!=', 'draft')])
        for instance in instances:
            try:
                with self.env.cr.savepoint():
                    client = instance._client()
                    info = remote.read_agent_state(client)
                    instance._store_remote_state(info, transport=client.transport)
            except RpcError as error:
                with self.env.cr.savepoint():
                    instance.write({
                        'state': 'unreachable' if isinstance(
                            error, (RpcUnreachable, RpcAuthError)
                        ) else 'error',
                        'last_error': str(error)[:4000],
                        'last_sync': fields.Datetime.now(),
                    })
                    instance._log('cron', False, str(error))
            except Exception as error:  # pragma: no cover - never kill the cron
                _logger.exception("Guard console: unexpected error on %s", instance.display_name)
                with self.env.cr.savepoint():
                    instance.write({'state': 'error', 'last_error': str(error)[:4000]})
        return True
