# Part of dbc_db_guard_console. See LICENSE file for full copyright details.
"""Minimal Odoo RPC client used to drive remote instances.

Two transports, same API:

``json2``
    ``POST /json/2/<model>/<method>`` with an ``Authorization: Bearer <api key>``
    header and the target database in the ``X-Odoo-Database`` header. This is
    the Odoo 19 interface; it only accepts a **global scope API key**, never a
    password.

``jsonrpc``
    ``POST /jsonrpc`` with ``service=object`` / ``method=execute_kw``. Still
    available in Odoo 19 but logged as deprecated and scheduled for removal in
    Odoo 22. It accepts an API key or a password, which makes it the fallback
    for instances older than 19.0.

The ``auto`` transport tries ``json2`` first and silently falls back.
"""

import itertools
import logging

try:
    import requests
except ImportError:  # pragma: no cover - requests ships with Odoo
    requests = None

_logger = logging.getLogger(__name__)

TRANSPORTS = ('auto', 'json2', 'jsonrpc')
DEFAULT_TIMEOUT = 20


class RpcError(Exception):
    """Base error for every remote failure."""


class RpcUnreachable(RpcError):
    """The instance could not be contacted at all."""


class RpcAuthError(RpcError):
    """The credentials were refused."""


class RpcCallError(RpcError):
    """The instance answered with an error."""


class RpcTransportUnsupported(RpcError):
    """The endpoint does not exist on that instance."""


class OdooClient:
    """RPC client for one remote Odoo instance."""

    def __init__(self, base_url, db_name, login, secret,
                 transport='auto', timeout=DEFAULT_TIMEOUT, verify_ssl=True):
        if requests is None:  # pragma: no cover
            raise RpcError("The python 'requests' library is not available.")
        self.base_url = (base_url or '').rstrip('/')
        self.db_name = db_name or ''
        self.login = login or ''
        self.secret = secret or ''
        self.requested_transport = transport if transport in TRANSPORTS else 'auto'
        self.timeout = timeout or DEFAULT_TIMEOUT
        self.verify_ssl = bool(verify_ssl)
        self._transport = None
        self._uid = None
        self._ids = itertools.count(1)
        self._session = requests.Session()

    def __str__(self):
        return '%s [%s]' % (self.base_url, self.db_name)

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    @property
    def transport(self):
        """Resolve (once) which transport this instance speaks."""
        if self._transport:
            return self._transport
        if self.requested_transport != 'auto':
            self._transport = self.requested_transport
            return self._transport
        try:
            self._json2_call('ir.module.module', 'search_read', {
                'domain': [('name', '=', 'base')], 'fields': ['id'], 'limit': 1,
            })
            self._transport = 'json2'
        except (RpcTransportUnsupported, RpcAuthError) as error:
            _logger.info(
                "%s: /json/2 unusable (%s), falling back on /jsonrpc.", self, error,
            )
            self._transport = 'jsonrpc'
        return self._transport

    def _post(self, path, payload, headers=None):
        url = self.base_url + path
        try:
            return self._session.post(
                url, json=payload, headers=headers or {}, timeout=self.timeout,
                verify=self.verify_ssl, allow_redirects=False,
            )
        except requests.RequestException as error:
            raise RpcUnreachable("%s: %s" % (url, error)) from error

    # ------------------------------------------------------------------
    # json2
    # ------------------------------------------------------------------
    def _json2_call(self, model, method, payload):
        response = self._post(
            '/json/2/%s/%s' % (model, method), payload,
            headers={
                'Authorization': 'Bearer %s' % self.secret,
                'X-Odoo-Database': self.db_name,
            },
        )
        if response.status_code in (401, 403):
            raise RpcAuthError(self._error_message(
                response, "the API key was refused (a global scope API key is required)",
            ))
        if response.status_code == 404 and not self._looks_like_json(response):
            # no /json/2 route at all: Odoo 18 or older
            raise RpcTransportUnsupported("/json/2 is not available on %s" % self.base_url)
        if response.status_code >= 400:
            raise RpcCallError(self._error_message(response, "call failed"))
        try:
            return response.json()
        except ValueError as error:
            raise RpcCallError("%s: unexpected answer to %s.%s" % (self, model, method)) from error

    # ------------------------------------------------------------------
    # jsonrpc
    # ------------------------------------------------------------------
    def _jsonrpc_call(self, service, method, args):
        response = self._post('/jsonrpc', {
            'jsonrpc': '2.0',
            'method': 'call',
            'id': next(self._ids),
            'params': {'service': service, 'method': method, 'args': args},
        })
        if response.status_code == 404:
            raise RpcTransportUnsupported("/jsonrpc is not available on %s" % self.base_url)
        if response.status_code >= 400 and not self._looks_like_json(response):
            raise RpcCallError(self._error_message(response, "call failed"))
        try:
            body = response.json()
        except ValueError as error:
            raise RpcCallError("%s: unexpected answer to %s.%s" % (self, service, method)) from error
        if body.get('error'):
            message = self._jsonrpc_error_message(body['error'])
            if 'AccessDenied' in str(body['error']) or 'Access denied' in message:
                raise RpcAuthError(message)
            raise RpcCallError(message)
        return body.get('result')

    def _authenticate(self):
        if self._uid:
            return self._uid
        uid = self._jsonrpc_call('common', 'authenticate', [
            self.db_name, self.login, self.secret, {},
        ])
        if not uid:
            raise RpcAuthError(
                "%s: the instance refused the credentials of %r." % (self, self.login)
            )
        self._uid = uid
        return uid

    def _execute_kw(self, model, method, args, kwargs=None):
        return self._jsonrpc_call('object', 'execute_kw', [
            self.db_name, self._authenticate(), self.secret,
            model, method, args, kwargs or {},
        ])

    # ------------------------------------------------------------------
    # public, transport independent API
    # ------------------------------------------------------------------
    def probe(self):
        """Check connectivity and credentials, return the server version."""
        version = self.server_version()
        return {'transport': self.transport, 'version': version}

    def server_version(self):
        """Version of the remote Odoo, read from the ``base`` module."""
        modules = self.search_read(
            'ir.module.module', [('name', '=', 'base')], ['latest_version'], limit=1,
        )
        return (modules[0].get('latest_version') or '') if modules else ''

    def search_read(self, model, domain, fields, limit=None):
        if self.transport == 'json2':
            payload = {'domain': domain, 'fields': fields}
            if limit:
                payload['limit'] = limit
            return self._json2_call(model, 'search_read', payload) or []
        kwargs = {'fields': fields}
        if limit:
            kwargs['limit'] = limit
        return self._execute_kw(model, 'search_read', [domain], kwargs) or []

    def read(self, model, ids, fields):
        if self.transport == 'json2':
            return self._json2_call(model, 'read', {'ids': ids, 'fields': fields}) or []
        return self._execute_kw(model, 'read', [ids, fields]) or []

    def create(self, model, values):
        if self.transport == 'json2':
            result = self._json2_call(model, 'create', {'vals_list': [values]})
            return result[0] if isinstance(result, list) else result
        return self._execute_kw(model, 'create', [values])

    def call_on(self, model, ids, method):
        """Call a no-argument method on existing records."""
        if self.transport == 'json2':
            return self._json2_call(model, method, {'ids': ids})
        return self._execute_kw(model, method, [ids])

    # ------------------------------------------------------------------
    # plain HTTP probing, used to verify what the instance really serves
    # ------------------------------------------------------------------
    def http_status(self, path):
        """Status code served by the instance for ``path`` (no redirects)."""
        url = self.base_url + path
        try:
            response = self._session.get(
                url, timeout=self.timeout, verify=self.verify_ssl, allow_redirects=False,
            )
        except requests.RequestException as error:
            raise RpcUnreachable("%s: %s" % (url, error)) from error
        return response.status_code

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _looks_like_json(response):
        return 'json' in (response.headers.get('Content-Type') or '').lower()

    def _error_message(self, response, fallback):
        detail = ''
        if self._looks_like_json(response):
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                detail = str(
                    body.get('message')
                    or (body.get('error') or {}).get('message')
                    or body.get('name') or ''
                )
        if not detail:
            detail = (response.text or '').strip()[:300]
        return "%s: HTTP %s, %s%s" % (
            self, response.status_code, fallback, ' (%s)' % detail if detail else '',
        )

    @staticmethod
    def _jsonrpc_error_message(error):
        data = error.get('data') or {}
        return str(
            data.get('message') or error.get('message') or data.get('name') or error
        ).strip()[:500]
