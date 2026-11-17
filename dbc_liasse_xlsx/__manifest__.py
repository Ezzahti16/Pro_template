# -*- coding: utf-8 -*-
{
    'name': 'DBC - Liasse Fiscale XLSX (Maroc)',
    'version': '19.0.4.0.0',
    'category': 'Accounting/Accounting',
    'summary': "Export the Moroccan liasse fiscale as the DGI's own Excel workbook",
    'description': """
Liasse fiscale - Excel export
=============================
Adds a "Liasse XLSX" button to the 25 Moroccan liasse reports, producing the
same workbook the DGI forms are filled on, with this company's figures in it.

The workbook is not drawn from scratch: it is the filed form itself, emptied
of its data and kept as a template, so fonts, borders, column widths, merged
cells and print setup are the ones of the paper liasse. Each value is written
into the cell the form puts it in; the map was built by aligning every report
line with its row on the form and was checked cell by cell against a real
filed liasse.

The three tableaux the DGI workbook prints over two pages as one table - the
CPC, the Detail des postes du CPC and the Principales methodes d'evaluation -
are delivered as a single sheet each. The Bilan keeps its two sheets: actif
and passif are two tables on different column grids.

The cover page is filled from the company record - raison sociale, identifiant
fiscal, address, and the city and date above the signature.

Values come from the same engine as the EDI XML export, so the workbook and
the XML always agree.
""",
    'author': 'DBC',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['dbc_liasse_fiscale', 'dbc_liasse_edi'],
    'data': [
        'views/res_company_views.xml',
    ],
    'installable': True,
    'application': False,
}
