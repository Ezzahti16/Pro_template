# Part of dbc_db_access_guard. See LICENSE file for full copyright details.

from odoo import api, fields, models
from odoo.exceptions import ValidationError

from ..guard import config_store as store


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dbc_dbguard_enabled = fields.Boolean(
        string="Protect the database manager",
        default=True,
        config_parameter=store.PARAMS['enabled'],
        help="Answer a 404 on /web/database/* and serve the database manager "
             "on the secret path below instead.",
    )
    dbc_dbguard_secret_path = fields.Char(
        string="Secret database manager path",
        default=store.DEFAULT_SECRET_PATH,
        size=128,
        config_parameter=store.PARAMS['secret_path'],
        help="Base URL of the hidden database manager, for instance "
             "/database/technique-acces. The manager is served on this path, "
             "the selector on <path>/selector.",
    )
    dbc_dbguard_block_selector = fields.Boolean(
        string="Block the database selector too",
        default=True,
        config_parameter=store.PARAMS['block_selector'],
        help="Also answer a 404 on /web/database/selector. On a multi-database "
             "instance without dbfilter, users then have to log in with "
             "/web/login?db=<database> (or /odoo?db=<database>).",
    )
    dbc_dbguard_block_db_rpc = fields.Boolean(
        string="Block the 'db' RPC service",
        default=True,
        config_parameter=store.PARAMS['block_db_rpc'],
        help="Refuse the database management RPC service (list, create, drop, "
             "dump, restore over /jsonrpc and /xmlrpc). Without this, anybody "
             "can still list the database names of this instance.",
    )
    dbc_dbguard_allowed_ips = fields.Char(
        string="Restrict to IP addresses",
        config_parameter=store.PARAMS['allowed_ips'],
        help="Optional comma separated list of IP addresses or networks "
             "(10.0.0.5, 192.168.1.0/24) allowed to reach the secret path and "
             "to use the 'db' RPC service. Leave empty to allow any address.",
    )

    dbc_dbguard_manager_url = fields.Char(
        string="Database manager URL", compute='_compute_dbc_dbguard_urls',
    )
    dbc_dbguard_selector_url = fields.Char(
        string="Database selector URL", compute='_compute_dbc_dbguard_urls',
    )
    dbc_dbguard_server_wide = fields.Boolean(
        string="Loaded as a server-wide module", compute='_compute_dbc_dbguard_server_wide',
    )

    @api.depends('dbc_dbguard_secret_path')
    def _compute_dbc_dbguard_urls(self):
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url', '')
        base_url = (base_url or '').rstrip('/')
        for settings in self:
            try:
                path = store.normalize_secret_path(settings.dbc_dbguard_secret_path)
            except ValueError:
                settings.dbc_dbguard_manager_url = False
                settings.dbc_dbguard_selector_url = False
                continue
            settings.dbc_dbguard_manager_url = base_url + path
            settings.dbc_dbguard_selector_url = base_url + path + '/selector'

    def _compute_dbc_dbguard_server_wide(self):
        is_server_wide = store.is_server_wide()
        for settings in self:
            settings.dbc_dbguard_server_wide = is_server_wide

    # ------------------------------------------------------------------
    # the data_dir store is the source of truth: the database manager is
    # served without any database, so the middleware cannot read parameters
    # ------------------------------------------------------------------
    @api.model
    def get_values(self):
        values = super().get_values()
        guard_config = store.read_config()
        values.update(
            dbc_dbguard_enabled=guard_config['enabled'],
            dbc_dbguard_secret_path=guard_config['secret_path'],
            dbc_dbguard_block_selector=guard_config['block_selector'],
            dbc_dbguard_block_db_rpc=guard_config['block_db_rpc'],
            dbc_dbguard_allowed_ips=', '.join(guard_config['allowed_ips']) or False,
        )
        return values

    def set_values(self):
        for settings in self:
            try:
                secret_path = store.normalize_secret_path(settings.dbc_dbguard_secret_path)
            except ValueError as error:
                raise ValidationError(str(error)) from error
            try:
                allowed_ips = store.normalize_allowed_ips(settings.dbc_dbguard_allowed_ips)
            except ValueError as error:
                raise ValidationError(str(error)) from error

            settings.dbc_dbguard_secret_path = secret_path
            settings.dbc_dbguard_allowed_ips = ', '.join(allowed_ips) or False

        super().set_values()

        for settings in self:
            store.write_config({
                'enabled': settings.dbc_dbguard_enabled,
                'secret_path': settings.dbc_dbguard_secret_path,
                'block_selector': settings.dbc_dbguard_block_selector,
                'block_db_rpc': settings.dbc_dbguard_block_db_rpc,
                'allowed_ips': settings.dbc_dbguard_allowed_ips,
            }, updated_by=self.env.cr.dbname)
