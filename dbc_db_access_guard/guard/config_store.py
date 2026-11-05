# Part of dbc_db_access_guard. See LICENSE file for full copyright details.
"""Persistence of the database-access-guard configuration.

The Odoo database manager and database selector are served by ``auth="none"``
routes that work **without** any database: for such requests Odoo builds its
routing map from the server-wide modules only and no cursor is ever opened.
A guard that must answer those requests therefore cannot read its settings
from ``ir.config_parameter`` at request time.

The settings are consequently mirrored into a small JSON file stored inside the
Odoo ``data_dir``.  That file is the source of truth for the WSGI middleware
(:mod:`odoo.addons.dbc_db_access_guard.guard.middleware`); ``ir.config_parameter``
keeps a copy so the values stay visible and scriptable from inside Odoo.

Because the guard protects the *server process* (the database manager is not
attached to any database), the configuration is instance-wide: the last
database that saves the settings wins.
"""

import ipaddress
import json
import logging
import os
import re
import tempfile

from odoo.tools import config

_logger = logging.getLogger(__name__)

#: default secret base path, applied when the module is installed
DEFAULT_SECRET_PATH = '/database/technique-acces'

#: prefix of every stock database-management route
DB_MANAGER_PREFIX = '/web/database'

#: ``/web/database/<endpoint>`` routes reachable through the secret base path.
#: An empty tail (the secret path alone) is mapped to ``manager``.
DB_MANAGER_ENDPOINTS = frozenset({
    'manager',
    'selector',
    'create',
    'duplicate',
    'drop',
    'backup',
    'restore',
    'change_password',
    'list',
})

PARAM_PREFIX = 'dbc_db_access_guard.'
PARAMS = {
    'enabled': PARAM_PREFIX + 'enabled',
    'secret_path': PARAM_PREFIX + 'secret_path',
    'block_selector': PARAM_PREFIX + 'block_selector',
    'block_db_rpc': PARAM_PREFIX + 'block_db_rpc',
    'allowed_ips': PARAM_PREFIX + 'allowed_ips',
}

#: Secure-by-default values, used when no configuration file exists yet.
DEFAULTS = {
    'enabled': True,
    'secret_path': DEFAULT_SECRET_PATH,
    'block_selector': True,
    'block_db_rpc': True,
    'allowed_ips': (),
}

STORE_DIRNAME = 'dbc_db_access_guard'
STORE_FILENAME = 'guard.json'

#: a secret path may not steal one of those first url segments
RESERVED_SEGMENTS = frozenset({
    'web', 'odoo', 'website', 'mail', 'static', 'longpolling', 'websocket',
    'jsonrpc', 'xmlrpc', 'xmlrpc2', 'my', 'shop', 'auth', 'api', 'report',
    'base', 'bus', 'pos', 'point_of_sale', 'calendar', 'im_livechat',
    'payment', 'terms', 'blog', 'event', 'forum', 'slides', 'survey', 'sign',
    'contactus', 'helpdesk', 'jobs', 'partner', 'google_account',
    'microsoft_account', 'robots.txt', 'sitemap.xml', 'favicon.ico',
    '.well-known', 'test_http',
})

_SEGMENT_RE = re.compile(r'^(?:/[A-Za-z0-9._~-]+)+$')
_MIN_LENGTH = 5
_MAX_LENGTH = 128


# ---------------------------------------------------------------------------
# value normalisation
# ---------------------------------------------------------------------------
def collapse_path(path):
    """Return ``path`` with duplicate slashes collapsed and no trailing slash."""
    path = re.sub(r'/{2,}', '/', path or '')
    if len(path) > 1:
        path = path.rstrip('/')
    return path or '/'


def normalize_secret_path(raw):
    """Validate and normalise a secret base path.

    :raise ValueError: when the path cannot be used safely.
    """
    path = (raw or '').strip()
    if not path:
        raise ValueError("The secret database-manager path cannot be empty.")
    if any(char.isspace() for char in path):
        raise ValueError("The secret database-manager path cannot contain spaces.")
    if not path.startswith('/'):
        path = '/' + path
    path = collapse_path(path)

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
    if path.lower().startswith(DB_MANAGER_PREFIX):
        raise ValueError(
            "The secret database-manager path cannot start with %r, that is "
            "exactly the prefix this module blocks." % DB_MANAGER_PREFIX
        )
    return path


def normalize_allowed_ips(raw):
    """Normalise an IP/CIDR allow-list.

    Accepts a string (comma, semicolon or whitespace separated) or an iterable.

    :return: tuple of normalised entries
    :raise ValueError: when an entry is neither an IP address nor a network
    """
    if not raw:
        return ()
    if isinstance(raw, str):
        entries = re.split(r'[\s,;]+', raw)
    else:
        entries = [str(part) for part in raw]

    result, invalid = [], []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            invalid.append(entry)
            continue
        text = str(network)
        if text not in result:
            result.append(text)
    if invalid:
        raise ValueError(
            "Invalid IP address or network: %s. Use addresses such as "
            "'10.0.0.5' or networks such as '192.168.1.0/24'." % ', '.join(invalid)
        )
    return tuple(result)


def to_bool(value):
    """Interpret Odoo, JSON and textual truth values."""
    if isinstance(value, str):
        return value.strip().lower() not in ('', '0', 'false', 'none', 'off', 'no')
    return bool(value)


def sanitize(values):
    """Build a complete, safe configuration dict out of ``values``."""
    result = dict(DEFAULTS)
    values = values or {}

    for key in ('enabled', 'block_selector', 'block_db_rpc'):
        if key in values:
            result[key] = to_bool(values[key])

    if values.get('secret_path'):
        try:
            result['secret_path'] = normalize_secret_path(values['secret_path'])
        except ValueError as error:
            _logger.warning(
                "Ignoring invalid secret database-manager path %r (%s), "
                "falling back on %r.", values['secret_path'], error, DEFAULT_SECRET_PATH,
            )

    result['allowed_ips'] = ()
    if values.get('allowed_ips'):
        try:
            result['allowed_ips'] = normalize_allowed_ips(values['allowed_ips'])
        except ValueError as error:
            _logger.warning("Ignoring invalid database-manager IP allow-list (%s).", error)
    return result


# ---------------------------------------------------------------------------
# file storage
# ---------------------------------------------------------------------------
def store_dir():
    return os.path.join(config['data_dir'], STORE_DIRNAME)


def store_file():
    return os.path.join(store_dir(), STORE_FILENAME)


_DEFAULT_CONFIG = sanitize(None)
_cache = {'stamp': None, 'values': None}


def read_config():
    """Return the effective guard configuration. Never raises.

    The returned dict is shared and must not be mutated by callers.
    """
    path = store_file()
    try:
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        # No configuration file: stay secure, with the documented default path.
        return _DEFAULT_CONFIG

    if _cache['stamp'] == stamp and _cache['values'] is not None:
        return _cache['values']

    try:
        with open(path, 'r', encoding='utf-8') as handle:
            values = sanitize(json.load(handle))
    except Exception:  # pragma: no cover - corrupted file
        _logger.exception(
            "Unreadable database-access-guard configuration %s, falling back "
            "on the secure defaults.", path,
        )
        return _DEFAULT_CONFIG

    _cache['values'] = values
    _cache['stamp'] = stamp
    return values


def write_config(values, updated_by=None):
    """Atomically persist ``values`` as the instance-wide guard configuration."""
    data = sanitize(values)
    payload = dict(data, allowed_ips=list(data['allowed_ips']), version=1)
    if updated_by:
        payload['updated_by_database'] = updated_by

    directory = store_dir()
    os.makedirs(directory, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:  # pragma: no cover - non posix filesystem
        pass

    handle, tmp_path = tempfile.mkstemp(dir=directory, prefix='.guard-', suffix='.tmp')
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:  # pragma: no cover - non posix filesystem
            pass
        os.replace(tmp_path, store_file())
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    invalidate_cache()
    return data


def invalidate_cache():
    _cache['stamp'] = None
    _cache['values'] = None


def is_server_wide():
    """Tell whether this module is loaded as a server-wide module.

    Only then is the guard guaranteed to be active for *every* request, including
    those a freshly started worker receives before any registry has been loaded.
    """
    return 'dbc_db_access_guard' in (config['server_wide_modules'] or [])
