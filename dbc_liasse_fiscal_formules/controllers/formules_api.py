# -*- coding: utf-8 -*-
"""JSON API exposing the Morocco fiscal formulas (ma.fiscal.setting).

Routes (all return JSON, testable directly in a browser or with curl):
    GET  /api/ma/formules                  -> {"count": N, "formules": {"<edi>": "<formule>", ...}}
    GET  /api/ma/formules?client_id=&year=  -> same shape, scoped to that client's fiscal year
    GET  /api/ma/formules/<edi>             -> {"edi": "...", "table_": n, "formule": "..",
                                                "modele": n, "code_comptable": "..", "libelle": ".."}
    POST /api/ma/clients/register           -> {"client_id": N} (register-or-return, see below)
    POST /api/ma/clients/unregister          -> {"ok": true} (deletes a client and its formulas)
    POST /api/ma/clients/formules/update     -> {"ok": true, "formule": ".."} (see below)
"""
import json

from odoo import http
from odoo.http import request


class MaFormulesApi(http.Controller):

    @http.route('/api/ma/formules', type='http', auth='public', methods=['GET'], csrf=False)
    def get_formules(self, client_id=None, year=None):
        """Formulas keyed by EDI code (what the report engine needs).

        With no params: the master/template set (unchanged behaviour).
        With both `client_id` and `year`: that one client's snapshot for
        that fiscal year.
        """
        client_id = int(client_id) if client_id else None
        year = int(year) if year else None
        formules = request.env['ma.fiscal.setting'].sudo()._get_formules(
            client_id=client_id, year=year,
        )
        return request.make_json_response({
            'count': len(formules),
            'formules': formules,
        })

    @http.route('/api/ma/formules/<string:edi>', type='http', auth='public',
                methods=['GET'], csrf=False)
    def get_formule(self, edi):
        """A single formula definition by EDI code."""
        rec = request.env['ma.fiscal.setting'].sudo().search([('edi', '=', edi)], limit=1)
        if not rec:
            return request.make_json_response({'edi': edi, 'found': False})
        return request.make_json_response({
            'edi': rec.edi,
            'table_': rec.table_,
            'formule': rec.formule,
            'modele': rec.modele,
            'code_comptable': rec.code_comptable,
            'libelle': rec.libelle,
            'found': True,
        })

    @http.route('/api/ma/clients/register', type='http', auth='public',
                methods=['POST'], csrf=False)
    def register_client(self):
        """Register a client (idempotent: same name -> same client_id) and
        make sure it has a full formulas snapshot for every fiscal year
        from 2020 up to the current year, cloned from the master set.

        Body: {"name": "<company name>"}
        Header: X-Register-Secret: <ma_formules.register_secret>

        This is the only write route on this API; the shared secret is
        required to prevent an unauthenticated caller from spawning
        thousands of rows per request.
        """
        icp = request.env['ir.config_parameter'].sudo()
        expected_secret = icp.get_param('ma_formules.register_secret')
        provided_secret = request.httprequest.headers.get('X-Register-Secret')
        if not expected_secret or provided_secret != expected_secret:
            return request.make_json_response({'error': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except json.JSONDecodeError:
            return request.make_json_response({'error': 'invalid JSON body'}, status=400)

        name = (payload.get('name') or '').strip()
        if not name:
            return request.make_json_response({'error': 'missing "name"'}, status=400)

        Client = request.env['ma.fiscal.client'].sudo()
        Setting = request.env['ma.fiscal.setting'].sudo()
        client = Client.search([('name', '=', name)], limit=1)
        if not client:
            client = Client.create({'name': name})
        # Idempotent: years already cloned for this client are skipped, so
        # calling this again (retry, or a later run after a new year began)
        # only fills in whatever is missing.
        Setting._clone_master_for_client(client)
        return request.make_json_response({'client_id': client.id})

    @http.route('/api/ma/clients/unregister', type='http', auth='public',
                methods=['POST'], csrf=False)
    def unregister_client(self):
        """Delete a client and every formula row it owns (every fiscal
        year) — never the master/template rows, which stay untouched.

        Body: {"client_id": N}
        Header: X-Register-Secret: <ma_formules.register_secret> (same
        shared secret as /api/ma/clients/register).
        """
        icp = request.env['ir.config_parameter'].sudo()
        expected_secret = icp.get_param('ma_formules.register_secret')
        provided_secret = request.httprequest.headers.get('X-Register-Secret')
        if not expected_secret or provided_secret != expected_secret:
            return request.make_json_response({'error': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except json.JSONDecodeError:
            return request.make_json_response({'error': 'invalid JSON body'}, status=400)

        client_id = payload.get('client_id')
        if not client_id:
            return request.make_json_response({'error': 'missing "client_id"'}, status=400)

        client = request.env['ma.fiscal.client'].sudo().browse(int(client_id)).exists()
        if not client:
            return request.make_json_response({'error': 'unknown client_id'}, status=404)

        request.env['ma.fiscal.setting'].sudo().search(
            [('client_id', '=', client.id)]
        ).unlink()
        client.unlink()
        return request.make_json_response({'ok': True})

    @http.route('/api/ma/clients/formules/update', type='http', auth='public',
                methods=['POST'], csrf=False)
    def update_client_formule(self):
        """Set one EDI code's formula for one client's one fiscal year —
        never the master/template rows, so an edit here can't leak into
        any other client's reports.

        Body: {"client_id": N, "year": N, "edi": "<code>", "formule": "<text>"}
        Header: X-Register-Secret: <ma_formules.register_secret> (same
        shared secret as /api/ma/clients/register — same trust level: a
        legitimate dbc_liasse_fiscale installation, not a per-client key).
        """
        icp = request.env['ir.config_parameter'].sudo()
        expected_secret = icp.get_param('ma_formules.register_secret')
        provided_secret = request.httprequest.headers.get('X-Register-Secret')
        if not expected_secret or provided_secret != expected_secret:
            return request.make_json_response({'error': 'forbidden'}, status=403)

        try:
            payload = json.loads(request.httprequest.data or b'{}')
        except json.JSONDecodeError:
            return request.make_json_response({'error': 'invalid JSON body'}, status=400)

        client_id = payload.get('client_id')
        year = payload.get('year')
        edi = (payload.get('edi') or '').strip()
        formule = payload.get('formule')
        if not client_id or not year or not edi or formule is None:
            return request.make_json_response(
                {'error': 'missing "client_id", "year", "edi" or "formule"'}, status=400,
            )

        try:
            new_formule = request.env['ma.fiscal.setting'].sudo()._update_formula(
                int(client_id), int(year), edi, str(formule),
            )
        except ValueError as exc:
            return request.make_json_response({'error': str(exc)}, status=400)

        return request.make_json_response({'ok': True, 'formule': new_formule})
