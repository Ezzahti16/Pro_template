# -*- coding: utf-8 -*-
{
    'name': 'DBC - Liasse Fiscale XLSX (Maroc)',
    'version': '19.0.6.2.0',
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

The module also builds the Etat des ventes workbook, a separate file of a
single sheet, because the Etat des ventes is a separate declaration and not
one of the 25 tableaux. It is laid out as the four tables of the BILAN
workbook's "Etat ventes" sheet - detail of sales per client with their ICE,
sundry local customers, export customers, and the total - stacked one under
the other instead of side by side, so every amount falls in the same two
columns. Its figures come from dbc_liasse_edi, which computes them for the
matching EDI file, so the workbook and that file cannot disagree. Both are
produced from the Declaration screen.
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
