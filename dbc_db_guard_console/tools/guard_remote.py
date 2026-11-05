# Part of dbc_db_guard_console. See LICENSE file for full copyright details.
"""Read and write the Database Access Guard settings of a remote instance.

Everything here goes through **standard Odoo models**
(``ir.module.module``, ``res.config.settings``), so the agent module
``dbc_db_access_guard`` needs no API of its own and is never modified.

Why ``res.config.settings`` and not ``ir.config_parameter``:

* reading it returns the *effective* configuration, because the agent's
  ``get_values()`` reads the mirror file in the instance ``data_dir``, which is
  what its middleware actually obeys. The system parameters are only a copy and
  can be stale (someone may have edited the file by hand);
* writing it runs the agent's ``set_values()``, which validates the path and
  rewrites that mirror file. Writing the parameters alone would change nothing.

Applying a configuration is therefore the exact equivalent of opening
*Settings* on the remote instance and pressing *Save*.
"""

import re

AGENT_MODULE = 'dbc_db_access_guard'
SETTINGS_MODEL = 'res.config.settings'

#: guard setting -> field name on the remote ``res.config.settings``
SETTINGS_FIELDS = {
    'enabled': 'dbc_dbguard_enabled',
    'secret_path': 'dbc_dbguard_secret_path',
    'block_selector': 'dbc_dbguard_block_selector',
    'block_db_rpc': 'dbc_dbguard_block_db_rpc',
    'allowed_ips': 'dbc_dbguard_allowed_ips',
}
EXTRA_FIELDS = ('dbc_dbguard_server_wide',)

#: the stock URL that must answer 404 once the guard is active
STOCK_MANAGER_PATH = '/web/database/manager'

#: Path rules, kept identical to the agent's ``config_store.normalize_secret_path``.
#: They are duplicated on purpose: the console must be able to validate a path
#: without having the agent module installed next to it.
DEFAULT_SECRET_PATH = '/database/technique-acces'
RESERVED_SEGMENTS = frozenset({
    'web', 'odoo', 'website', 'mail', 'static', 'longpolling', 'websocket',
    'jsonrpc', 'xmlrpc', 'xmlrpc2', 'my', 'shop', 'auth', 'api', 'report',
    'base', 'bus', 'pos', 'point_of_sale', 'calendar', 'im_livechat',
    'payment', 'terms', 'blog', 'event', 'forum', 'slides', 'survey', 'sign',
    'contactus', 'helpdesk', 'jobs', 'partner', 'google_account',
    'microsoft_account', 'robots.txt', 'sitemap.xml', 'favicon.ico',
    '.well-known', 'test_http', 'json',
})
_SEGMENT_RE = re.compile(r'^(?:/[A-Za-z0-9._~-]+)+$')
_MIN_LENGTH = 5
_MAX_LENGTH = 128


def normalize_secret_path(raw):
    """Validate a secret base path. :raise ValueError: when unusable."""
    path = (raw or '').strip()
    if not path:
        raise ValueError("The secret database-manager path cannot be empty.")
    if any(char.isspace() for char in path):
        raise ValueError("The secret database-manager path cannot contain spaces.")
    if not path.startswith('/'):
        path = '/' + path
    path = re.sub(r'/{2,}', '/', path)
    if len(path) > 1:
        path = path.rstrip('/')

    if not _MIN_LENGTH <= len(path) <= _MAX_LENGTH:
        raise ValueError(
            "The secret database-manager path must be between %s and %s "
            "characters long (got %r)." % (_MIN_LENGTH, _MAX_LENGTH, path)
        )
    if not _SEGMENT_RE.match(path):
        raise ValueError(
            "The secret database-manager path %r is invalid: start it with '/' "
            "and use only letters, digits and - _ . ~ /" % path
        )
    segments = path.strip('/').split('/')
    if any(segment in ('.', '..') for segment in segments):
        raise ValueError("The secret database-manager path cannot contain '.' or '..' segments.")
    if segments[0].lower() in RESERVED_SEGMENTS:
        raise ValueError(
            "'/%s' is reserved by Odoo and cannot be used as the first part of "
            "the secret database-manager path." % segments[0]
        )
    if any(segment.lower() == 'static' for segment in segments):
        raise ValueError("The secret database-manager path cannot contain a 'static' segment.")
    if path.lower().startswith('/web/database'):
        raise ValueError(
            "The secret database-manager path cannot start with '/web/database', "
            "that is exactly the prefix the guard blocks."
        )
    return path


def normalize_base_url(raw):
    """Validate the base URL of an instance. :raise ValueError: when unusable."""
    url = (raw or '').strip().rstrip('/')
    if not url:
        raise ValueError("The instance URL cannot be empty.")
    if not re.match(r'^https?://[^\s/]+', url):
        raise ValueError(
            "The instance URL must start with http:// or https:// and contain a "
            "host, for example https://client.example.com (got %r)." % raw
        )
    return url


# ---------------------------------------------------------------------------
# remote reads / writes
# ---------------------------------------------------------------------------
def read_agent_state(client):
    """Return what the remote instance currently enforces.

    :return: ``{'installed': bool, 'version': str, 'server_wide': bool,
               'config': {...} or None}``
    """
    modules = client.search_read(
        'ir.module.module', [('name', '=', AGENT_MODULE)],
        ['state', 'latest_version'], limit=1,
    )
    if not modules or modules[0].get('state') != 'installed':
        return {
            'installed': False,
            'version': (modules[0].get('latest_version') or '') if modules else '',
            'state': (modules[0].get('state') or 'uninstalled') if modules else 'absent',
            'server_wide': False,
            'config': None,
        }

    fields = list(SETTINGS_FIELDS.values()) + list(EXTRA_FIELDS)
    settings_id = client.create(SETTINGS_MODEL, {})
    values = client.read(SETTINGS_MODEL, [settings_id], fields)
    values = values[0] if values else {}
    return {
        'installed': True,
        'version': modules[0].get('latest_version') or '',
        'state': 'installed',
        'server_wide': bool(values.get('dbc_dbguard_server_wide')),
        'config': {
            key: _clean(key, values.get(field))
            for key, field in SETTINGS_FIELDS.items()
        },
    }


def apply_config(client, config):
    """Push ``config`` onto the remote instance (runs the agent's validation)."""
    values = {}
    for key, field in SETTINGS_FIELDS.items():
        value = config[key]
        if key == 'allowed_ips':
            value = value or ''
        values[field] = value
    settings_id = client.create(SETTINGS_MODEL, values)
    client.call_on(SETTINGS_MODEL, [settings_id], 'execute')


def verify_config(client, config):
    """Check over plain HTTP what the instance really serves.

    :return: ``(ok, message)``. ``ok`` is None when the check is not
             conclusive and must not trigger a rollback.
    """
    if config['enabled'] and config['allowed_ips']:
        return None, (
            "HTTP verification skipped: the guard only answers the allow-listed "
            "addresses (%s) and this server is probably not one of them."
            % config['allowed_ips']
        )

    secret_path = config['secret_path']
    if config['enabled']:
        secret_status = client.http_status(secret_path)
        stock_status = client.http_status(STOCK_MANAGER_PATH)
        if secret_status == 200 and stock_status == 404:
            return True, (
                "Verified: %s answers 200, %s answers 404."
                % (secret_path, STOCK_MANAGER_PATH)
            )
        return False, (
            "Verification failed: %s answers %s (expected 200) and %s answers "
            "%s (expected 404)."
            % (secret_path, secret_status, STOCK_MANAGER_PATH, stock_status)
        )

    stock_status = client.http_status(STOCK_MANAGER_PATH)
    if stock_status == 200:
        return True, "Verified: %s answers 200 again." % STOCK_MANAGER_PATH
    return None, (
        "Could not confirm deactivation: %s answers %s. A reverse proxy or "
        "list_db=False may be hiding it independently of the guard."
        % (STOCK_MANAGER_PATH, stock_status)
    )


def _clean(key, value):
    if key in ('enabled', 'block_selector', 'block_db_rpc'):
        return bool(value)
    return (value or '') if isinstance(value, str) else ('' if not value else str(value))
