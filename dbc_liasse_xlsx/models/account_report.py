# -*- coding: utf-8 -*-
"""Liasse fiscale -> the DGI's own Excel workbook.

The workbook is not drawn here. data/liasse_template.xlsx IS the filed form,
emptied of one company's figures, so everything that makes it look like the
liasse - fonts, borders, merged cells, column widths, row heights, print
setup - is carried by the template and never reconstructed. This module only
writes values into the cells models/cellmap.py points at.

The template departs from the filed workbook in two ways, both of them so the
sheets follow the reports rather than one company's paperwork. The four
tableaux the DGI prints over two pages are one table here, not two tables
under one sheet name - the repeated column header and the CPC's carry-forward
line are gone. And tableau 15 carries a row for every line its report has,
including the two subtotals the printed form has no line for.

The figures come from dbc_liasse_edi's own collection pass, the one that feeds
the EDI XML. That is deliberate: the two exports are then the same numbers
rendered twice, and cannot drift apart.
"""
import datetime
import io
import logging

from odoo import _, models
from odoo.exceptions import UserError
from odoo.tools import file_open

from odoo.addons.dbc_liasse_edi.models.account_report import (
    _ANNEX_TABLES,
    _REPORT_TABLEAU,
)
from .cellmap import (
    ALIASES,
    ANNEX,
    BLANK_ZERO_ROWS,
    FLAT,
    FORM_TOTALS,
    HEADER,
    HEADER_EXTRA_FIELDS,
    SHEET_TITLES,
    SUBTOTAL_COLUMNS,
    X_MARK_TEXT,
)

_logger = logging.getLogger(__name__)

_TEMPLATE = 'dbc_liasse_xlsx/data/liasse_template.xlsx'


def _is_blank(value):
    return value is None or value is False or (isinstance(value, str) and not value.strip())


class AccountReportCustomHandlerXlsx(models.AbstractModel):
    # Same base as the EDI exporter: the 25 liasse reports are split across two
    # unrelated handler families, and _dbc_edi_tableau_for() is what keeps the
    # button off every other report in the database.
    _inherit = 'account.report.custom.handler'

    # ------------------------------------------------------------------ button

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options=previous_options)
        if self._dbc_edi_tableau_for(report):
            options.setdefault('buttons', []).append({
                'name': _('Liasse XLSX'),
                'sequence': 26,
                'action': 'export_file',
                'action_param': 'dbc_liasse_xlsx_export',
                'file_export_type': _('XLSX'),
            })

    # --------------------------------------------------------------- generator

    def dbc_liasse_xlsx_export(self, options):
        """Build the whole liasse workbook for the period selected on the
        report. Every tableau is produced, not just the report clicked on: the
        file is the liasse, not one of its pages."""
        report = self.env['account.report'].browse(options['report_id'])
        company = self._dbc_edi_company(report, options)
        date_from, date_to = self._dbc_edi_exercice_bounds(company, options)
        content = self._dbc_xlsx_build(
            company, date_from, date_to, bool(options.get('all_entries')))
        return {
            'file_name': '%s.xlsx' % self._dbc_edi_basename(company, date_to),
            'file_content': content,
            'file_type': 'xlsx',
        }

    # ------------------------------------------------------------------ values

    def _dbc_xlsx_values(self, company, date_from, date_to, all_entries):
        """{tableau: {'flat': {code: value},
                      'rows': {numero: {code: value}},
                      'totals': {code: value}}}

        Mirrors dbc_liasse_edi's collection pass by calling the very same
        methods, so a cell that is wrong here is wrong in the XML too, and
        vice versa.
        """
        year = str(date_to.year)
        period_options = self._dbc_edi_period_options(date_from, date_to, all_entries)
        out = {}
        for xmlid, tableau_id in _REPORT_TABLEAU.items():
            target = self.env.ref('dbc_liasse_fiscale.%s' % xmlid, raise_if_not_found=False)
            if not target:
                continue
            target_options = target.get_options(dict(period_options))
            entry = {
                'flat': self._dbc_edi_report_values(target, target_options),
                'rows': {},
                'totals': {},
                'extra': {},
            }
            if tableau_id in _ANNEX_TABLES:
                for code, value, numero in self._dbc_edi_annex_rows(
                        tableau_id, company, year, date_from, date_to):
                    entry['rows'].setdefault(numero, {})[code] = value
                for code, value, _numero in self._dbc_edi_annex_totals(
                        tableau_id, company, year, date_from, date_to):
                    entry['totals'][code] = value
            out[str(tableau_id)] = entry

        # extraFields: values the liasse attaches to a tableau rather than to
        # one of its cells (cahier des charges 4.3.1.2). Most are the exercice
        # dates, already written in every sheet header; the one the form also
        # prints inside the page is tableau 16's total of the exercice's
        # dotations, which HEADER_EXTRA_FIELDS places.
        all_values = {}
        for entry in out.values():
            all_values.update(entry['flat'])
        for tableau, cells in HEADER_EXTRA_FIELDS.items():
            entry = out.get(tableau)
            if not entry:
                continue
            for code, value in self._dbc_edi_extra_fields(
                    int(tableau), company, str(date_to.year), date_from, date_to, all_values):
                if str(code) in cells:
                    entry['extra'][str(code)] = value
        return out

    # ----------------------------------------------------------------- writing

    def _dbc_xlsx_build(self, company, date_from, date_to, all_entries):
        try:
            import openpyxl
        except ImportError:
            raise UserError(_(
                "The Python library 'openpyxl' is required to export the liasse "
                "as an Excel workbook, and is not installed on this server."))

        with file_open(_TEMPLATE, 'rb') as stream:
            workbook = openpyxl.load_workbook(stream)

        anchors = self._dbc_xlsx_anchors(workbook)
        self._dbc_xlsx_write_headers(workbook, company, date_from, date_to, anchors)
        for sheet_name, title in SHEET_TITLES.items():
            if title and sheet_name in workbook.sheetnames:
                workbook[sheet_name]['A1'].value = title

        values = self._dbc_xlsx_values(company, date_from, date_to, all_entries)
        written = skipped = 0

        for tableau, entry in values.items():
            flat = dict(entry['flat'])
            flat.update(entry['totals'])
            for code, value in flat.items():
                places = self._dbc_xlsx_places(tableau, code)
                if not places:
                    skipped += 1
                    continue
                for place in places:
                    if self._dbc_xlsx_put(workbook, anchors, place, code, value,
                                          in_detail_row=False):
                        written += 1
            for numero, cells in entry['rows'].items():
                for code, value in cells.items():
                    place = self._dbc_xlsx_row_place(tableau, code, numero)
                    if place is None:
                        skipped += 1
                        continue
                    if self._dbc_xlsx_put(
                            workbook, anchors, place, code, value, in_detail_row=True,
                            tableau_blanks_zero_rows=tableau in BLANK_ZERO_ROWS):
                        written += 1
            written += self._dbc_xlsx_subtotals(workbook, anchors, tableau, entry['rows'])
            written += self._dbc_xlsx_form_totals(workbook, anchors, tableau, entry['rows'])
            for code, value in entry.get('extra', {}).items():
                place = HEADER_EXTRA_FIELDS.get(tableau, {}).get(code)
                if place and self._dbc_xlsx_put(workbook, anchors, place, code, value):
                    written += 1

        _logger.info(
            "Liasse XLSX for %s (%s..%s): %d cells written, %d not mapped",
            company.display_name, date_from, date_to, written, skipped)

        stream = io.BytesIO()
        workbook.save(stream)
        return stream.getvalue()

    def _dbc_xlsx_places(self, tableau, code):
        """Every cell a non-repeating value is printed in: its own cell, the
        foot line of a table of free rows, and - for a few section totals the
        form repeats next to their heading - a second copy (see ALIASES)."""
        places = []
        cell = FLAT.get(tableau, {}).get(code)
        if cell:
            places.append(cell)
        for block in ANNEX.get(tableau, []):
            column = (block.get('totals') or {}).get(code)
            if column and block.get('totals_row'):
                places.append((block['sheet'], block['totals_row'], column))
        alias = ALIASES.get(tableau, {}).get(code)
        if alias:
            places.append(alias)
        return places

    def _dbc_xlsx_row_place(self, tableau, code, numero):
        """Cell of one repeating detail row: record n lands on first + n - 1.

        Every table of free rows works this way, the turnover bands of the
        Calcul de l'IS included: the wording printed next to them in a filed
        liasse is the filer's own text, not the form's, so the workbook takes
        both the wording and the amount from the record.
        """
        for block in ANNEX.get(tableau, []):
            column = block['codes'].get(code)
            if not column:
                continue
            if numero is None or numero > block['slots']:
                # The paper form has a fixed number of rows; a liasse with more
                # records than that cannot be printed on it. The XML has no
                # such limit, which is why this is only logged.
                _logger.warning(
                    "Liasse XLSX: tableau %s has more rows than the form's %d "
                    "slots; row %s left out of the workbook (it is still in the "
                    "XML export)", tableau, block['slots'], numero)
                return None
            return (block['sheet'], block['first'] + numero - 1, column)
        return None

    def _dbc_xlsx_subtotals(self, workbook, anchors, tableau, rows):
        """Totals the form prints on a line of their own beside a section of
        free rows - tableau 3 above its rows, tableau 15 below them.

        The liasse gives these totals no code of their own: the same code
        names the detail column, and the EDI export sends it as detail rows
        carrying a numeroLigne. So the figure is summed from the rows written
        rather than read from a value of its own.
        """
        columns = SUBTOTAL_COLUMNS.get(tableau)
        if not columns:
            return 0
        written = 0
        for block in ANNEX.get(tableau, []):
            subtotal_row = block.get('subtotal_row')
            if not subtotal_row:
                continue
            for column in columns:
                codes = [c for c, col in block['codes'].items() if col == column]
                total = 0.0
                found = False
                for numero, cells in rows.items():
                    if numero is None or numero > block['slots']:
                        continue
                    for code in codes:
                        value = cells.get(code)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            total += value
                            found = True
                if not found or abs(total) < 0.005:
                    # A section with nothing in it is left blank, like every
                    # other nil line of the form.
                    continue
                place = anchors.get((block['sheet'], subtotal_row, column),
                                    (block['sheet'], subtotal_row, column))
                workbook[place[0]].cell(row=place[1], column=place[2]).value = total
                written += 1
        return written

    def _dbc_xlsx_form_totals(self, workbook, anchors, tableau, rows):
        """The foot line of a table of free rows, where the liasse defines no
        code for it. The Etat de repartition du capital is the one such case:
        its nombre de titres and montant columns are totalled on the form, but
        only the rows themselves are declared."""
        spec = FORM_TOTALS.get(tableau)
        if not spec:
            return 0
        written = 0
        for block in ANNEX.get(tableau, []):
            if block['sheet'] != spec['sheet']:
                continue
            for column in spec['columns']:
                codes = [c for c, col in block['codes'].items() if col == column]
                total, found = 0.0, False
                for numero, cells in rows.items():
                    if numero is None or numero > block['slots']:
                        continue
                    for code in codes:
                        value = cells.get(code)
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            total += value
                            found = True
                if not found:
                    continue
                place = anchors.get((spec['sheet'], spec['row'], column),
                                    (spec['sheet'], spec['row'], column))
                workbook[place[0]].cell(row=place[1], column=place[2]).value = total
                written += 1
        return written

    def _dbc_xlsx_put(self, workbook, anchors, place, code, value,
                      in_detail_row=False, tableau_blanks_zero_rows=False):
        if _is_blank(value):
            return False
        # A line of the form that comes to nothing is left empty, not filled
        # with 0,00 - the filed liasse has a blank in every such cell. A zero
        # inside a detail row IS printed, because there it means the amount
        # really is nil (tableau 16 shows a 0 in its dotations column); the
        # exception is tableau 3, whose rows carry either a + or a - amount
        # and never both, so the empty side is left blank there too.
        if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 0.005:
            if not in_detail_row or tableau_blanks_zero_rows:
                return False
        sheet, row, column = anchors.get(tuple(place), tuple(place))
        if sheet not in workbook.sheetnames:
            return False
        if code in X_MARK_TEXT:
            # Tableau 19 marks the contract type with an X in the XML; the
            # workbook spells it out in a single column instead.
            if str(value).strip().upper() != 'X':
                return False
            value = X_MARK_TEXT[code]
        if isinstance(value, (datetime.date, datetime.datetime)):
            # The workbook holds its dates as jj/mm/aaaa text, not as date
            # values, so a generated file sorts and prints like the filed one.
            value = value.strftime('%d/%m/%Y')
        workbook[sheet].cell(row=row, column=column).value = value
        return True

    # ------------------------------------------------------------------ header

    def _dbc_xlsx_anchors(self, workbook):
        """A value can only be written to the top-left cell of a merged range;
        every other cell of the range is a read-only stub."""
        anchors = {}
        for sheet in workbook.worksheets:
            for rng in sheet.merged_cells.ranges:
                for row in range(rng.min_row, rng.max_row + 1):
                    for col in range(rng.min_col, rng.max_col + 1):
                        anchors[(sheet.title, row, col)] = (
                            sheet.title, rng.min_row, rng.min_col)
        return anchors

    def _dbc_xlsx_write_headers(self, workbook, company, date_from, date_to, anchors):
        """Company, identifiant fiscal and exercice, in the places the form
        prints them - which differ from sheet to sheet."""
        identifiant = (company.ma_identifiant_fiscal or '').strip()
        decision = company.ma_date_decision_affectation
        texts = {
            'company': company.display_name,
            'identifiant': _("Identifiant fiscal: %s", identifiant),
            'identifiant_bare': identifiant,
            'exercice': _("Exercice du %(start)s au %(end)s",
                          start=date_from.strftime('%d/%m/%Y'),
                          end=date_to.strftime('%d/%m/%Y')),
            # the cover page, out of the company record rather than out of the
            # workbook the template was made from
            'raison_sociale': company._dbc_liasse_raison_sociale(),
            'adresse': company._dbc_liasse_adresse(),
            'lieu_date': company._dbc_liasse_lieu_date(),
            # no field holds the taxe professionnelle yet, so its line stays
            # as blank as it is on an unfilled form
            'taxe_professionnelle': None,
            # "Decision du ..." on the Tableau d'Affectation. The liasse has no
            # code for it, so it comes from the company rather than the
            # accounts; left blank when unset, as the form would be.
            'decision_affectation': decision.strftime('%d/%m/%Y') if decision else None,
        }
        for sheet_name, fields in HEADER.items():
            if sheet_name not in workbook.sheetnames:
                continue
            sheet = workbook[sheet_name]
            for field, addr in fields.items():
                text = texts.get(field)
                if text is None:
                    continue
                cell = sheet[addr]
                place = anchors.get((sheet_name, cell.row, cell.column),
                                    (sheet_name, cell.row, cell.column))
                sheet.cell(row=place[1], column=place[2]).value = text
