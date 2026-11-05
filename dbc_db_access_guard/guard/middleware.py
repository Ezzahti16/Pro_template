# Part of dbc_db_access_guard. See LICENSE file for full copyright details.
"""WSGI level guard for the Odoo database manager, selector and ``db`` RPC service.

Why a WSGI middleware instead of a controller override?

* ``/web/database/*`` routes are ``auth="none"`` routes.  When a request carries
  no database (no session, multi-database instance), Odoo serves it through
  ``Application.nodb_routing_map``, which is built from the *server-wide*
  modules only: a controller defined in a regular custom module is simply not
  part of that routing map and could never override those routes.
* The routing maps are cached, so a route whose URL depends on a user setting
  cannot be registered through ``@http.route`` and changed at runtime.

Hooking ``odoo.http.Application.__call__`` solves both problems: it runs before
any routing happens, for every request, with or without a database, and it can
read the current secret path on each request.

Behaviour
---------
* ``<secret base path>[/<endpoint>]`` is internally rewritten to
  ``/web/database/<endpoint>`` (the bare secret path maps to ``manager``), and
  the HTML/redirects sent back are rewritten the other way round so the whole
  database manager keeps working under the secret path.
* ``/web/database/*`` requested directly answers a plain ``404 Not Found``.
* the ``db`` RPC service (``/jsonrpc``, ``/xmlrpc/2/db``, ...) is refused unless
  the call comes from an authorised database-manager request or from an
  allow-listed IP address.
"""

import ipaddress
import logging
import threading

import werkzeug.exceptions

from odoo.tools import config

from . import config_store as store

_logger = logging.getLogger(__name__)

_local = threading.local()

_PATCH_FLAG = '_dbc_db_access_guard_installed'

#: headers of responses that may be rewritten (small, text based)
_HTML_CONTENT_TYPES = ('text/html', 'application/xhtml+xml')


# ---------------------------------------------------------------------------
# request helpers
# ---------------------------------------------------------------------------
def client_ip(environ):
    """Best effort client IP, honouring ``proxy_mode`` like Odoo does."""
    if config['proxy_mode']:
        forwarded = environ.get('HTTP_X_FORWARDED_FOR')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return environ.get('REMOTE_ADDR') or ''


def ip_allowed(environ, guard_config):
    """Tell whether the request comes from an allow-listed address.

    An empty allow-list allows every address.
    """
    allowed = guard_config.get('allowed_ips') or ()
    if not allowed:
        return True
    if environ is None:
        return False
    try:
        address = ipaddress.ip_address(client_ip(environ))
    except ValueError:
        return False
    for entry in allowed:
        try:
            if address in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:  # pragma: no cover - sanitised upstream
            continue
    return False


def is_authorized_request():
    """True while serving a request that came in through the secret path."""
    return getattr(_local, 'authorized', False)


def current_environ():
    return getattr(_local, 'environ', None)


# ---------------------------------------------------------------------------
# path matching
# ---------------------------------------------------------------------------
def match_secret_path(path, secret_path):
    """Return the ``/web/database`` endpoint requested through ``secret_path``.

    :return: the endpoint name, ``''`` for the bare secret path, or ``None``
             when ``path`` is unrelated to the secret path.
    """
    normalized = store.collapse_path(path)
    if normalized == secret_path:
        return ''
    if normalized.startswith(secret_path + '/'):
        return normalized[len(secret_path) + 1:]
    return None


def is_blocked_path(path, guard_config):
    """Tell whether ``path`` targets a protected database-management route."""
    normalized = store.collapse_path(path)
    prefix = store.DB_MANAGER_PREFIX
    if normalized != prefix and not normalized.startswith(prefix + '/'):
        return False
    endpoint = normalized[len(prefix):].lstrip('/')
    if endpoint == 'selector' and not guard_config['block_selector']:
        # the selector is explicitly left public (needed to log in on a
        # multi-database instance without dbfilter)
        return False
    return True


# ---------------------------------------------------------------------------
# responses
# ---------------------------------------------------------------------------
def _not_found(environ, start_response):
    """Answer a stock 404, exactly like a non existing URL would."""
    return werkzeug.exceptions.NotFound()(environ, start_response)


def _rewrite_location(value, secret_path):
    """Point a redirect back to the secret path instead of /web/database."""
    if store.DB_MANAGER_PREFIX in value:
        return value.replace(store.DB_MANAGER_PREFIX, secret_path)
    return value


def _rewrite_headers(headers, secret_path):
    result = []
    for name, value in headers:
        if name.lower() == 'location':
            value = _rewrite_location(value, secret_path)
        result.append((name, value))
    return result


def _is_html(headers):
    for name, value in headers:
        if name.lower() == 'content-type':
            return any(kind in value.lower() for kind in _HTML_CONTENT_TYPES)
    return False


def _serve_authorized(application, environ, start_response, secret_path, original_call):
    """Serve a rewritten database-manager request.

    HTML answers are rewritten so that every ``/web/database/...`` link, form
    action and redirect of the stock database manager stays inside the secret
    path.  Anything else (database dumps in particular) is streamed untouched.
    """
    state = {}
    buffered = []

    def inner_start_response(status, headers, exc_info=None):
        headers = _rewrite_headers(headers, secret_path)
        if exc_info is None and _is_html(headers):
            # defer: the body is rewritten, hence Content-Length changes
            state['status'] = status
            state['headers'] = headers
            return buffered.append
        return start_response(status, headers, exc_info)

    _local.authorized = True
    try:
        result = original_call(application, environ, inner_start_response)
    finally:
        _local.authorized = False

    if 'status' not in state:
        # streamed as-is (dumps, redirects, errors...)
        return result

    try:
        body = b''.join(buffered) + b''.join(result)
    finally:
        close = getattr(result, 'close', None)
        if close is not None:
            close()

    body = body.replace(store.DB_MANAGER_PREFIX.encode(), secret_path.encode())
    headers = [
        (name, value) for name, value in state['headers']
        if name.lower() != 'content-length'
    ]
    headers.append(('Content-Length', str(len(body))))
    start_response(state['status'], headers)
    return [body]


# ---------------------------------------------------------------------------
# patches
# ---------------------------------------------------------------------------
def _build_application_call(original_call):
    def guarded_call(self, environ, start_response):
        guard_config = store.read_config()
        if not guard_config['enabled']:
            return original_call(self, environ, start_response)

        path = environ.get('PATH_INFO') or '/'
        secret_path = guard_config['secret_path']
        previous_environ = getattr(_local, 'environ', None)
        _local.environ = environ
        try:
            endpoint = match_secret_path(path, secret_path)
            if endpoint is not None:
                if not ip_allowed(environ, guard_config):
                    _logger.warning(
                        "Database manager: secret path %s requested from "
                        "non allow-listed address %s, answering 404.",
                        path, client_ip(environ),
                    )
                    return _not_found(environ, start_response)
                if endpoint and endpoint not in store.DB_MANAGER_ENDPOINTS:
                    return _not_found(environ, start_response)
                environ['PATH_INFO'] = '%s/%s' % (
                    store.DB_MANAGER_PREFIX, endpoint or 'manager',
                )
                return _serve_authorized(
                    self, environ, start_response, secret_path, original_call,
                )

            if is_blocked_path(path, guard_config):
                _logger.info(
                    "Database manager: blocked %s %s from %s (reachable on %s).",
                    environ.get('REQUEST_METHOD', 'GET'), path,
                    client_ip(environ), secret_path,
                )
                return _not_found(environ, start_response)

            return original_call(self, environ, start_response)
        finally:
            _local.environ = previous_environ

    guarded_call.__wrapped__ = original_call
    return guarded_call


def _build_db_service_dispatch(original_dispatch):
    def guarded_dispatch(method, params):
        guard_config = store.read_config()
        if guard_config['enabled'] and guard_config['block_db_rpc']:
            environ = current_environ()
            if not is_authorized_request() and not (
                environ is not None
                and guard_config.get('allowed_ips')
                and ip_allowed(environ, guard_config)
            ):
                _logger.warning(
                    "Database manager: refused the 'db' RPC service "
                    "(method %s) from %s.", method,
                    client_ip(environ) if environ is not None else 'an internal caller',
                )
                raise werkzeug.exceptions.NotFound()
        return original_dispatch(method, params)

    guarded_dispatch.__wrapped__ = original_dispatch
    return guarded_dispatch


def install():
    """Install the guard patches. Safe to call several times."""
    import odoo.http
    import odoo.service.db

    application = odoo.http.Application
    if getattr(application, _PATCH_FLAG, False):
        return False

    application.__call__ = _build_application_call(application.__call__)
    odoo.service.db.dispatch = _build_db_service_dispatch(odoo.service.db.dispatch)
    setattr(application, _PATCH_FLAG, True)

    guard_config = store.read_config()
    _logger.info(
        "Database access guard active%s: database manager on %s, "
        "/web/database/* answers 404.%s",
        '' if guard_config['enabled'] else ' (currently disabled)',
        guard_config['secret_path'],
        '' if store.is_server_wide() else (
            " Add 'dbc_db_access_guard' to server_wide_modules in your odoo.conf "
            "to guarantee the guard is loaded before any request is served."
        ),
    )
    return True
