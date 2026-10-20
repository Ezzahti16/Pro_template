# -*- coding: utf-8 -*-
{
    'name': 'DBC - Liasse Fiscale (Maroc)',
    'version': '19.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Moroccan CGNC "Bilan (Actif)" statement computed from legacy EDI codes and formules',
    'description': """
Moroccan Bilan (Actif) report
=============================
Creates a new accounting report ("Bilan (Actif)") for Odoo 19, reproducing the
layout of the legacy fiscal software:

* Every cell is bound to an EDI code (table ma.fiscal.setting, imported from
  the fiscal_settings table of the legacy database).
* Each EDI code has a "Formule" that can combine:
    - EDI('xxx') : reference to another EDI code (computed recursively);
    - SE('xxx')  : accounts whose code starts with 'xxx'. A positive term takes
      the total DEBIT of the journal items on those accounts, a negative term
      (-SE(...)) takes the total CREDIT;
    - ASE('xxx') : signed balance (debit - credit) of those accounts;
    - numeric constants and + - * / arithmetic.
* Columns: Brut / Amortissements et Provisions / Net N are computed from the
  very beginning up to the end of the selected period; Net N-1 is computed up
  to the beginning of the current period (end of previous year).
""",
    'author': 'DBC',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['account_reports'],
    'data': [
        'security/ir.model.access.csv',
        'security/groups.xml',
        'views/res_config_settings_views.xml',
        'data/ma.fiscal.setting.csv',
        'data/bilan_actif_report.xml',
        'data/bilan_passif_report.xml',
        'data/bilan_cpc_report.xml',
        'data/bilan_prov_report.xml',
        'data/bilan_fisc_report.xml',
        'data/bilan_immob_report.xml',
        'data/bilan_esg_report.xml',
        'data/bilan_detail_cpc_report.xml',
        'data/bilan_amort_report.xml',
        'data/bilan_tva15_report.xml',
        'data/bilan_affect_report.xml',
        'data/bilan_fusion_report.xml',
        'data/bilan_stocks_report.xml',
        'data/bilan_eval_report.xml',
        'data/bilan_derog_report.xml',
        'data/bilan_devises_report.xml',
        'data/bilan_financement_report.xml',
        'data/bilan_is_report.xml',
        'data/bilan_cession_immob_report.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dbc_liasse_fiscale/static/src/account_report.scss',
            'dbc_liasse_fiscale/static/src/js/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
