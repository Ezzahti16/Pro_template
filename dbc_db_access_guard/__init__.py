# Part of dbc_db_access_guard. See LICENSE file for full copyright details.

import logging

from . import guard
from . import models
from .guard import config_store as store

_logger = logging.getLogger(__name__)

# Patch the WSGI application as soon as this module is imported, i.e. when it is
# loaded as a server-wide module (recommended) or when the registry of a
# database where it is installed is loaded.
guard.middleware.install()


def post_init_hook(env):
    """Turn the guard on, keeping an existing secret path if there is one."""
    guard_config = dict(store.read_config())
    guard_config['enabled'] = True
    store.write_config(guard_config, updated_by=env.cr.dbname)
    _set_parameters(env, guard_config)
    _logger.info(
        "Database access guard installed: /web/database/* now answers 404, "
        "the database manager is served on %s", guard_config['secret_path'],
    )


def uninstall_hook(env):
    """Give the stock URLs back, so uninstalling never locks anybody out."""
    guard_config = dict(store.read_config())
    guard_config['enabled'] = False
    store.write_config(guard_config, updated_by=env.cr.dbname)
    _logger.info(
        "Database access guard uninstalled: /web/database/manager is reachable again.",
    )


def _set_parameters(env, guard_config):
    parameters = env['ir.config_parameter'].sudo()
    parameters.set_param(store.PARAMS['enabled'], guard_config['enabled'])
    parameters.set_param(store.PARAMS['secret_path'], guard_config['secret_path'])
    parameters.set_param(store.PARAMS['block_selector'], guard_config['block_selector'])
    parameters.set_param(store.PARAMS['block_db_rpc'], guard_config['block_db_rpc'])
    parameters.set_param(store.PARAMS['allowed_ips'], ', '.join(guard_config['allowed_ips']))
