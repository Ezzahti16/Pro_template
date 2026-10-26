# -*- coding: utf-8 -*-
{
    'name': 'IADIS - Morocco Fiscal Formules (Data + API)',
    'version': '19.0.4.5.0',
    'category': 'Accounting/Accounting',
    'summary': 'Editable list of the legacy EDI formules (ma.fiscal.setting) with a JSON API',
    'description': """
Morocco Fiscal Formules (Data + API)
=====================================
Standalone data module holding the 2516 EDI codes and their formulas imported
from the legacy fiscal software (ma.fiscal.setting).

It provides:
* An editable list view and a search view (search by EDI name e.g. "EDI159",
  by libelle, etc.) to manage the formulas directly in Odoo.
* A JSON HTTP API to fetch the formulas needed to compute the reports:
    - GET /api/ma/formules                   -> {"formules": {"<edi>": "<formule>", ...}}
    - GET /api/ma/formules?client_id=&year=   -> same shape, for one client's fiscal year
    - GET /api/ma/formules/<edi>              -> {"edi": "...", "formule": "...", ...}
  The standard Odoo JSON-RPC / XML-RPC endpoints are available as well, so any
  other Odoo instance can read this data remotely.
* Per-client, per-fiscal-year formulas: rows with no client/year are the
  master/template set; a client registers once (POST /api/ma/clients/register,
  requires the `ma_formules.register_secret` shared secret) and gets a full
  snapshot of the master formulas cloned for every fiscal year from 2020 up
  to the current year. Re-calling registration for an existing client is
  idempotent and only fills in any missing years.
* Per-client, per-fiscal-year formula editing: POST
  /api/ma/clients/formules/update (same shared secret) sets one EDI code's
  formula for one client's one fiscal year only — never the master rows —
  self-healing the row if that client/year doesn't have one yet.
* A monthly cron automatically gives every registered client the current
  fiscal year's formulas once it starts, cloned from their own most recent
  prior year (never the master set) so any formula they edited carries
  forward instead of being reset.
* POST /api/ma/clients/unregister (same shared secret) deletes a client and
  every formula row it owns, for every fiscal year — the master/template
  rows are never touched.
""",
    'author': '',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['base', 'base_setup'],
    'data': [
        # 'security/groups.xml',
        'security/ir.model.access.csv',
        'data/ma.fiscal.setting.csv',
        'data/ir_cron.xml',
        'views/ma_fiscal_setting_views.xml',
        'views/ma_fiscal_setting_menus.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
}
