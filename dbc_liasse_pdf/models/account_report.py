# -*- coding: utf-8 -*-
"""Liasse fiscale -> a typeset PDF of the whole return.

The figures are the ones the 25 liasse reports show on screen, read straight
off them rather than out of the Excel export, and they are laid out as a
document meant to be read: a cover page out of the company record, then one
tableau to a page in the order of the liasse, each under a banded heading
carrying its number and title.

A tableau wider than seven columns is printed across the page. One that runs
past the bottom carries on over the page with its column header repeated.

Needs wkhtmltopdf, which Odoo already ships for its own reports.
"""
import io
import logging

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools.misc import find_in_path

from odoo.addons.dbc_liasse_edi.models.account_report import _REPORT_TABLEAU
from odoo.addons.dbc_liasse_xlsx.models.cellmap import ANNEX as _XLSX_ANNEX

from . import liasse_html

_logger = logging.getLogger(__name__)

# Which page of the paper liasse each tableau is. The DGI numbers its form
# 1 to 25; the codes the EDI file uses are unrelated to that numbering, and
# the Bilan is one tableau reported by two of ours.
_FORM_NUMBER = {
    '2': (1, 'Actif'), '1': (1, 'Passif'), '6': (2, None), '7': (3, None),
    '11': (4, None), '32': (5, None), '34': (6, None), '23': (7, None),
    '24': (8, None), '37': (9, None), '38': (10, None), '39': (11, None),
    '40': (12, None), '41': (13, None), '5': (14, None), '240': (15, None),
    '12': (16, None), '26': (17, None), '27': (18, None), '28': (19, None),
    '36': (20, None), '200': (21, None), '202': (23, None), '203': (24, None),
    '220': (25, None),
}
# Tableau 22 is a free-text statement with no figures in it: there is no
# report behind it, so it is printed as the blank form the workbook holds,
# and the liasse is whole.
_NO_REPORT = {
    22: {
        'title': "État des Changements de Méthodes",
        'columns': ["Nature des changements",
                    "Justification des changements",
                    "Influence sur le patrimoine, la situation financière "
                    "et les résultats"],
        'sections': ["I - Changement affectant les méthodes d'évaluation",
                     "II - Changement affectant les règles de présentation"],
        'rows': 4,
    },
}
# A tableau the form draws as SEVERAL tables, each read in columns of its
# own. Odoo's account.report holds one column layout per report, so the
# Tableau de financement is one report whose eight columns cover both of its
# tables and whose every line fills only the four that are its own; the screen
# splits it back in two on the code of the line, and the workbook gives each
# table its own block of rows on the sheet. Printing it as one grid would
# leave half of every line empty, so it is split here the same way.
#
# {report xml_id: ({subtitle, code prefix of its lines (None = all the rest),
#                   columns (first, last), stub}, ...)}
_SPLIT = {
    'bilan_financement_report': (
        {'subtitle': "I. Synthèse des masses du bilan",
         'code': 'FIN_SYNTH_', 'columns': (0, 4), 'stub': 'Masses'},
        {'subtitle': "II. Emplois et Ressources",
         'code': None, 'columns': (4, 8), 'stub': 'Rubriques'},
    ),
}
# How many rows to rule on a tableau that has nothing to show, where the form
# itself sets no number of them, and the most that are drawn in any case: a
# page holds about thirty ruled lines once the heading is on it, and a tableau
# with nothing in it is not worth a second page of blank ones.
_BLANK_ROWS = 8
_BLANK_ROWS_MAX = 30
# The first column of each tableau is headed by the form, not by the report.
_STUB = {
    1: 'Actif / Passif', 2: 'Postes', 4: 'Nature', 7: 'Rubriques',
    8: 'Nature', 9: 'Nature', 10: 'Date de cession ou de retrait',
    11: 'Raison sociale de la société émettrice', 13: 'Nom, prénom ou raison sociale',
    16: 'Immobilisations concernées', 18: 'Bénéficiaires', 19: 'Bailleur',
    20: 'Nature du stock',
}


# Columns the screen needs and the paper has none of: on the nine tableaux
# whose rows are typed in - crédit-bail, capital, dotations, emprunts,
# locations, titres, cessions, passage au résultat fiscal, calcul de l'impôt -
# account.report carries a last column for the pencil and the bin of the
# editable table. It is empty on every line and headed by nothing, and printed
# it is an empty strip down the right of the tableau.
_SCREEN_ONLY = {'ma_actions'}


# How a column is set, by what it holds: wording to the left, dates in the
# middle, anything counted or costed to the right.
_ALIGN = {'string': 'lbl', 'date': 'mid', 'datetime': 'mid', 'none': 'lbl'}


class AccountReportCustomHandlerPdf(models.AbstractModel):
    # Same base as the XLSX and EDI exporters: the 25 liasse reports are split
    # across two unrelated handler families, and _dbc_edi_tableau_for() is what
    # keeps the button off every other report in the database.
    _inherit = 'account.report.custom.handler'

    # ------------------------------------------------------------------ button

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        if self._dbc_edi_tableau_for(report):
            options.setdefault('buttons', []).append({
                'name': _('Liasse PDF'),
                'sequence': 27,
                'action': 'export_file',
                'action_param': 'dbc_liasse_pdf_export',
                'file_export_type': _('PDF'),
            })

    def dbc_liasse_pdf_export(self, options):
        """The whole liasse for the period selected on the report, every
        tableau, not just the one clicked on."""
        report = self.env['account.report'].browse(options['report_id'])
        company = self._dbc_edi_company(report, options)
        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        content = self._dbc_pdf_build(
            company, date_from, date_to, bool(options.get('all_entries')))
        return {
            'file_name': '%s.pdf' % self._dbc_edi_basename(company, date_to),
            'file_content': content,
            'file_type': 'pdf',
        }

    # ------------------------------------------------------------------ values

    def _dbc_pdf_cover(self, company, date_from, date_to):
        return {
            'raison_sociale': company._dbc_liasse_raison_sociale(),
            # nothing holds the taxe professionnelle yet, so its line is left
            # as blank as it is on an unfilled form
            'taxe_professionnelle': '',
            'identifiant': (company.ma_identifiant_fiscal or '').strip(),
            'adresse': company._dbc_liasse_adresse(),
            'exercice': _("Exercice du %(start)s au %(end)s",
                          start=date_from.strftime('%d/%m/%Y'),
                          end=date_to.strftime('%d/%m/%Y')),
            'lieu_date': company._dbc_liasse_lieu_date(),
        }

    def _dbc_pdf_columns(self, options):
        """The columns of the report that belong on the form."""
        return [column for column in options['columns']
                if column.get('expression_label') not in _SCREEN_ONLY]

    def _dbc_pdf_tableaux(self, company, date_from, date_to, all_entries):
        """Every tableau of the liasse, in the order the form prints them."""
        period_options = self._dbc_edi_period_options(date_from, date_to, all_entries)
        tableaux = []
        for xmlid, tableau_id in _REPORT_TABLEAU.items():
            number = _FORM_NUMBER.get(str(tableau_id))
            if not number:
                _logger.warning("Liasse PDF: tableau %s has no page on the form",
                                tableau_id)
                continue
            report = self.env.ref('dbc_liasse_fiscale.%s' % xmlid, raise_if_not_found=False)
            if not report:
                continue
            options = report.get_options(dict(period_options))
            printed = self._dbc_pdf_columns(options)
            columns = [column['name'] for column in printed]
            split = _SPLIT.get(xmlid)
            # a split tableau is totalled a table at a time, not across both
            lines = self._dbc_pdf_lines(report, options, mark=not split)
            if split:
                tableaux.append({
                    'number': number[0],
                    'part': number[1],
                    'title': report.name,
                    'blocks': self._dbc_pdf_blocks(split, options, lines),
                })
                continue
            if not lines:
                lines = self._dbc_pdf_blank_rows(
                    len(columns), self._dbc_pdf_free_rows(tableau_id))
            else:
                lines = self._dbc_pdf_fill_free_rows(
                    tableau_id, len(columns), lines)
            stub = self._dbc_pdf_stub(number[0], columns, lines, tableau_id)
            if stub is None:
                lines = self._dbc_pdf_fold_names(lines)
            tableaux.append({
                'number': number[0],
                'part': number[1],
                'title': report.name,
                'stub': stub,
                'columns': columns,
                'aligns': [_ALIGN.get(column.get('figure_type'), 'num')
                           for column in printed],
                'lines': lines,
            })
        for number, form in _NO_REPORT.items():
            lines = []
            for section in form['sections']:
                lines.append({'name': section, 'level': 0, 'heading': True,
                              'total': False, 'blank': False,
                              'cells': [''] * len(form['columns'])})
                lines.extend(self._dbc_pdf_blank_rows(len(form['columns']),
                                                      form['rows']))
            tableaux.append({'number': number, 'part': None,
                             'title': form['title'], 'stub': None,
                             'columns': form['columns'],
                             'aligns': ['lbl'] * len(form['columns']),
                             'lines': lines})
        tableaux.sort(key=lambda t: (t['number'], t['part'] or ''))
        return tableaux

    def _dbc_pdf_blocks(self, split, options, lines):
        """The tables a split tableau is made of: one block per table, each
        holding the lines that belong to it and only the columns it is read
        in.

        A line goes to the first block whose lines it is named like; the block
        that names no prefix takes everything left over, which is how the
        screen reads it too - table I's lines are the FIN_SYNTH_ ones and
        table II is the rest.
        """
        rest, buckets = list(lines), []
        for block in split:
            prefix = block.get('code')
            if prefix:
                buckets.append([line for line in rest
                                if line['code'].startswith(prefix)])
                rest = [line for line in rest
                        if not line['code'].startswith(prefix)]
            else:
                buckets.append(rest)
                rest = []
        out = []
        for block, own in zip(split, buckets):
            first, last = block['columns']
            columns = self._dbc_pdf_columns(options)[first:last]
            out.append({
                'subtitle': block['subtitle'],
                'stub': block['stub'],
                'columns': [column['name'] for column in columns],
                'aligns': [_ALIGN.get(column.get('figure_type'), 'num')
                           for column in columns],
                'lines': self._dbc_pdf_mark_totals(
                    [dict(line, cells=line['cells'][first:last])
                     for line in own]),
            })
        return out

    def _dbc_pdf_free_rows(self, tableau_id):
        """How many rows the paper form leaves to be written on.

        Taken from the same table of free rows the workbook is filled through,
        so an empty tableau is ruled with exactly the lines the form has and
        the two exports show the same blank.
        """
        blocks = _XLSX_ANNEX.get(str(tableau_id)) or []
        rows = sum(block['slots'] for block in blocks) or _BLANK_ROWS
        return min(rows, _BLANK_ROWS_MAX)

    def _dbc_pdf_fill_free_rows(self, tableau_id, width, lines):
        """A table of free rows holding nothing but its own totals line is a
        blank form too, and is ruled like one.

        Several of these reports - les titres de participation, les plus ou
        moins values sur cessions - print their Totaux line whether or not
        there is anything to total, so an empty one does not come back empty
        and is not caught above. The rows are ruled above that line, where the
        form leaves them.

        A tableau that has lines of its own, the passage au résultat fiscal
        and le calcul de l'impôt among them, is left alone: its free rows are
        a section of a table that is otherwise filled in.
        """
        if str(tableau_id) not in _XLSX_ANNEX:
            return lines
        if any(not (line['heading'] or line['total'] or line['blank'])
               for line in lines):
            return lines
        blanks = self._dbc_pdf_blank_rows(
            width, self._dbc_pdf_free_rows(tableau_id))
        for index, line in enumerate(lines):
            if line['total']:
                return lines[:index] + blanks + lines[index:]
        return lines + blanks

    def _dbc_pdf_blank_rows(self, width, count):
        """Ruled but empty rows, as the form prints them when there is
        nothing to declare."""
        return [{'name': '', 'level': 1, 'heading': False, 'total': False,
                 'blank': True, 'cells': [''] * width}
                for _index in range(count)]

    def _dbc_pdf_fold_names(self, lines):
        """With no column for the wording, a line that carries one anyway -
        the Totaux of a table of free rows - says it in the first cell of the
        table, which is where the form prints it."""
        for line in lines:
            if line.get('heading') or not line['name'].strip():
                continue
            if line['cells'] and not line['cells'][0].strip():
                line['cells'][0] = line['name']
                line['name'] = ''
        return lines

    def _dbc_pdf_stub(self, number, columns, lines, tableau_id=None):
        """The heading of the first column, or None when there should not be
        one.

        The liasse is reported two ways. Most tableaux name their lines and
        carry only figures in the columns, and those need a column for the
        wording. The tables of free rows - crédit-bail, capital, dotations and
        the rest - have a line per record with no name at all, and their first
        column already IS the wording; giving those a column of their own
        would print the heading twice over an empty strip.

        On a table of free rows a Totaux line is not a reason to open one
        either. The report names it because it has nowhere else to put the
        word, but the form prints it in the first column of the table itself,
        and a column of its own is an empty strip down the page with one word
        at the foot of it - which is what the dotations aux amortissements,
        the titres de participation and the plus ou moins values sur cessions
        looked like. The word is moved into the first cell instead (see
        _dbc_pdf_fold_names), so this is only said when that cell is free.

        This is asked of a table of free rows only. Elsewhere a line with no
        figures on it is a line of the form all the same - the categories of
        the plus-values en cas de fusion are the rows of that table - and the
        column for the wording is what they are read in.
        """
        # a heading spans the whole width, so it is not a reason to open a
        # column for the wording
        named = [line for line in lines
                 if line['name'].strip() and not line.get('heading')]
        if not named:
            return None
        if str(tableau_id) in _XLSX_ANNEX and all(
                line.get('total') and line['cells']
                and not line['cells'][0].strip() for line in named):
            return None
        stub = _STUB.get(number) or "Libellé"
        first = (columns[0] if columns else '').strip().lower()
        return "Libellé" if stub.strip().lower() == first else stub

    def _dbc_pdf_lines(self, report, options, mark=True):
        """The report's own lines, as the screen shows them.

        A line that comes to nothing is printed with its cells empty, the way
        a liasse is filled in: a nil line is left blank, never written 0,00.

        Each line carries the code of the report line behind it, which is what
        a split tableau is divided on.
        """
        # the places of the columns that are printed, and what each holds
        keep = [index for index, column in enumerate(options['columns'])
                if column.get('expression_label') not in _SCREEN_ONLY]
        kinds = [options['columns'][index].get('figure_type') for index in keep]
        # the codes of the whole report in one read, so asking a line for its
        # own below is a cache hit rather than a query
        report.line_ids.mapped('code')
        raw = report._get_lines(options)
        out = []
        for line in raw:
            columns = line.get('columns') or []
            cells, reported = [], False
            for place, index in enumerate(keep):
                column = columns[index] if index < len(columns) else {}
                value = column.get('no_format')
                text = column.get('name') or ''
                if value is False or text == 'False':
                    # an unset Char comes back as False in Odoo, and the
                    # report renders it literally; a blank stays blank
                    value, text = None, ''
                if value is not None or text.strip():
                    # the line has a figure of its own, nil or not
                    reported = True
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if abs(value) < 0.005:
                        text = ''
                    elif kinds[place] in ('monetary', 'float'):
                        # the tables of free rows are declared as plain floats
                        # and print a single decimal; an amount on a tax return
                        # carries two wherever it stands
                        text = liasse_html.money(value)
                cells.append(text)
            classes = line.get('class') or ''
            out.append({
                'name': line['name'],
                'code': self._dbc_pdf_line_code(report, line),
                'level': line.get('level') or 0,
                # A heading is a line the report carries NO cell for - the
                # title over the ones under it. A line whose amounts are all
                # nil is an ordinary line of the table that happens to be
                # blank, and must not be dressed up as a heading.
                'heading': not reported,
                'total': 'o_bold_tr' in classes,
                'blank': False,
                'cells': cells,
            })
        # a line with neither wording nor a figure is a blank row the report
        # offers for typing into, not something to print
        out = [line for line in out
               if line['name'].strip() or any(cell.strip() for cell in line['cells'])]
        return self._dbc_pdf_mark_totals(out) if mark else out

    def _dbc_pdf_line_code(self, report, line):
        """The code of the account.report.line a rendered line came from, or
        '' for a line a custom handler made up (the tables of free rows)."""
        model, record_id = report._get_model_info_from_id(line['id'])
        if model != 'account.report.line':
            return ''
        return self.env['account.report.line'].browse(record_id).code or ''

    def _dbc_pdf_mark_totals(self, lines):
        """A line that has lines under it is a total of them, and is set in
        bold like one.

        Some reports say so themselves, with o_bold_tr on the line; the Bilan
        and the Tableau des immobilisations say it only through the depth of
        the line, the sections sitting one level above their details. Where a
        report is flat - every line at the same depth, as the E.S.G. is - this
        marks nothing, and its own o_bold_tr is left to do the work.
        """
        depths = {line['level'] for line in lines if not line['heading']}
        if len(depths) > 1:
            top = min(depths)
            for line in lines:
                if not line['heading'] and line['level'] == top:
                    line['total'] = True
        return lines

    # --------------------------------------------------------------- generator

    def _dbc_pdf_build(self, company, date_from, date_to, all_entries):
        cover = self._dbc_pdf_cover(company, date_from, date_to)
        tableaux = self._dbc_pdf_tableaux(company, date_from, date_to, all_entries)
        documents = liasse_html.runs(cover, tableaux, cover['identifiant'])

        footer = '%s  —  %s' % (cover['raison_sociale'], cover['exercice'])
        pages, offset = [], 0
        for orientation, html in documents:
            pdf = self._dbc_pdf_wkhtmltopdf(html, orientation, footer, offset)
            offset += self._dbc_pdf_page_count(pdf)
            pages.append(pdf)
        content = self._dbc_pdf_merge(pages)

        _logger.info(
            "Liasse PDF for %s (%s..%s): %d tableaux over %d pages in %d run(s)",
            company.display_name, date_from, date_to, len(tableaux), offset,
            len(documents))
        return content

    def _dbc_pdf_page_count(self, pdf):
        from PyPDF2 import PdfReader
        return len(PdfReader(io.BytesIO(pdf)).pages)

    def _dbc_pdf_wkhtmltopdf(self, html, orientation, footer, offset):
        import os
        import subprocess
        import tempfile

        margin = '%dmm' % liasse_html.PAGE_MARGIN_MM
        command = [find_in_path('wkhtmltopdf'), '--quiet',
                   '--disable-local-file-access', '--encoding', 'utf-8',
                   # the layout is written in points and millimetres and is
                   # meant to land on the paper as written
                   '--dpi', '96', '--disable-smart-shrinking',
                   '--page-size', 'A4', '--orientation', orientation.capitalize(),
                   '--margin-top', margin, '--margin-bottom', '14mm',
                   '--margin-left', margin, '--margin-right', margin,
                   '--footer-font-name', 'Arial', '--footer-font-size', '7',
                   '--footer-spacing', '5',
                   '--footer-left', footer,
                   '--footer-right', 'Page [page]',
                   # the liasse is one document printed in several passes, so
                   # the numbering carries on from the pass before
                   '--page-offset', str(offset)]

        source = destination = None
        try:
            handle, source = tempfile.mkstemp(suffix='.html', prefix='liasse.')
            with os.fdopen(handle, 'wb') as stream:
                stream.write(html.encode('utf-8'))
            handle, destination = tempfile.mkstemp(suffix='.pdf', prefix='liasse.')
            os.close(handle)
            process = subprocess.run(command + [source, destination],
                                     capture_output=True, check=False)
            if process.returncode or not os.path.getsize(destination):
                raise UserError(_(
                    "The liasse could not be turned into a PDF.\n%s",
                    (process.stderr or b'').decode('utf-8', 'replace')[:500]))
            with open(destination, 'rb') as stream:
                return stream.read()
        finally:
            for path in (source, destination):
                if path and os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError:
                        _logger.warning("Liasse PDF: could not remove %s", path)

    def _dbc_pdf_merge(self, documents):
        """The runs back into one file, in the order of the liasse."""
        documents = [doc for doc in documents if doc]
        if len(documents) == 1:
            return documents[0]
        from PyPDF2 import PdfMerger
        merger = PdfMerger()
        try:
            for document in documents:
                merger.append(io.BytesIO(document))
            stream = io.BytesIO()
            merger.write(stream)
            return stream.getvalue()
        finally:
            merger.close()
