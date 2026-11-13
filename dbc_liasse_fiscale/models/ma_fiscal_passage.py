# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from .account_report import (
    MA_PASSAGE_MAX_ROWS,
    MA_PASSAGE_SECTIONS,
    _ma_fiscal_year_selection,
)


class MaFiscalPassageLine(models.Model):
    """One detail row of Tableau n°3's réintégrations / déductions blocks,
    for one company and one fiscal year.

    Same concept as ma.fiscal.capital.line and the other data-entry models:
    no EDI code or formula behind any field, just plain values the user keys
    in through the report's own header "+" button and per-row pencil/trash
    icons. What differs is where the rows are shown: these are injected
    inline into the static "Passage du Résultat Net Comptable au Résultat
    Net Fiscal" report under their own section line, rather than being the
    whole of a report of their own - see AccountReportHandlerMaPassage
    (_custom_line_postprocessor) in models/account_report.py.
    """
    _name = 'ma.fiscal.passage.line'
    _description = "Ligne de détail - Passage du Résultat Net Comptable au Résultat Net Fiscal"
    _order = 'section, sequence, id'
    _rec_name = 'intitule'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    section = fields.Selection(
        MA_PASSAGE_SECTIONS, string="Rubrique", required=True, default='reint_non_courante',
    )
    intitule = fields.Char(string="Intitulé")
    montant_plus = fields.Float(string="Montant (+)")
    montant_moins = fields.Float(string="Montant (-)")

    @api.constrains('section', 'annee_fiscale', 'company_id')
    def _check_max_rows_per_section(self):
        """The DGI grid gives each block five rows and no more (C_825 is
        'T3'!$B$19:$B$23). Refusing the sixth row here is the only place the
        limit can be enforced before the XML is built, where it would either
        be silently truncated or sent with a numeroLigne the schema has no
        slot for."""
        for line in self:
            count = self.search_count([
                ('company_id', '=', line.company_id.id),
                ('annee_fiscale', '=', line.annee_fiscale),
                ('section', '=', line.section),
            ])
            if count > MA_PASSAGE_MAX_ROWS:
                raise ValidationError(_(
                    "La rubrique « %(section)s » ne peut pas dépasser %(max)s lignes "
                    "pour l'exercice %(year)s : le tableau n°3 de la DGI ne prévoit "
                    "que %(max)s lignes par rubrique. Regroupez plusieurs natures sur "
                    "une même ligne si nécessaire.",
                    section=dict(MA_PASSAGE_SECTIONS)[line.section],
                    max=MA_PASSAGE_MAX_ROWS,
                    year=line.annee_fiscale,
                ))
