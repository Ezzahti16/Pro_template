# -*- coding: utf-8 -*-
{
    'name': 'DBC - Liasse Fiscale PDF (Maroc)',
    'version': '19.0.2.5.0',
    'category': 'Accounting/Accounting',
    'summary': "Print the Moroccan liasse fiscale as the DGI's own form, in PDF",
    'description': """
Liasse fiscale - PDF export
===========================
Adds a "Liasse PDF" button to the 25 Moroccan liasse reports. It prints the
whole return: a cover page drawn from the company record, then every tableau
on its own page, in the order the DGI form numbers them.

It is not the Excel export photographed. The figures and the wording are read
off the reports themselves and typeset as a document meant to be read - each
tableau under a banded heading carrying its number and title, a column header
that repeats when a tableau runs on to a second page, totals picked out, and
nil lines left blank the way a liasse is filled in.

A tableau the form draws as two tables read in columns of their own - the
Tableau de financement, synthèse des masses then emplois et ressources - is
printed as two tables, one under the other, as the screen and the workbook
keep them rather than run into a single grid.

The column the screen keeps for the pencil and the bin of an editable table
is not printed: the form has no such column and it is empty on every line.

A table of free rows is printed without a column for the wording, its
Totaux line included: the form has no such column, and the word is written in
the first cell of the table as the form writes it.

A tableau of more than six columns is printed across the page. One with
nothing to declare is ruled with the blank rows its form provides, so it can
be written on, rather than carrying a note - including one that comes back
holding nothing but its own Totaux line, as the titres de participation and
the plus ou moins values sur cessions do. Tableau 22, which is a free-text
statement with no report behind it, is printed as the blank form so the
liasse is whole.

Needs wkhtmltopdf, which Odoo already ships for its own reports.
""",
    'author': 'DBC',
    'website': '',
    'license': 'LGPL-3',
    'depends': ['dbc_liasse_xlsx'],
    'data': [],
    'installable': True,
    'application': False,
}
