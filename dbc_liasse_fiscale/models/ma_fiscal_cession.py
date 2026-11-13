# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalCessionLine(models.Model):
    """One disposal row of the "Tableau des Plus ou Moins Values sur
    Cessions ou Retraits d'Immobilisations" (tableau n° 10), for one
    company. Same concept as the other ma.fiscal.*.line models (see
    AccountReportHandlerMaCession / AccountReportHandlerMaDataEntry in
    models/account_report.py): plain values the user keys in through the
    report's own header "+" button and per-row pencil/trash icons.

    Three of the eight columns are COMPUTED and stored, because the DGI's
    own reference grid computes them from the other five and nothing else
    ('Liasse ExcelToXml V4.0 2026.xlsm', sheet T10, rows 10..19):

        E (valeur nette)  =C10-D10                      -> montant_brut - amort_cumules
        G (plus values)   =IF(F10>E10,F10-E10,"")       -> produit_cession - valeur_nette, if > 0
        H (moins values)  =IF(F10<E10,E10-F10,"")       -> valeur_nette - produit_cession, if > 0

    A disposal is either a plus value or a moins value, never both, so the
    two columns are mutually exclusive by construction and the one that
    does not apply stays at 0 - which is how the reference sheet leaves it
    (the "" of its IF, summed as 0 by the TOTAL row).

    The rows are scoped to an exercice by their own "annee_fiscale"
    Selection field, like ma.fiscal.capital.line / ma.fiscal.titres.line
    and unlike ma.fiscal.dotation.line: a cession belongs to the exercice
    it happened in and has no business reappearing on the next one.
    "Date de cession ou de retrait" is a liasse COLUMN of its own
    (codeEdi 1929), not the scoping key.
    """
    _name = 'ma.fiscal.cession.line'
    _description = "Ligne de cession - Tableau des Plus ou Moins Values sur Cessions ou Retraits d'Immobilisations"
    _order = 'date_cession, sequence, id'
    _rec_name = 'compte_principal'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    date_cession = fields.Date(string="Date de cession ou de retrait")
    compte_principal = fields.Char(string="Compte principal")
    montant_brut = fields.Float(string="Montant brut")
    amort_cumules = fields.Float(string="Amortissements cumulés")
    valeur_nette = fields.Float(
        string="Valeur nette d'amortissements",
        compute='_compute_valeur_nette', store=True, readonly=True,
    )
    produit_cession = fields.Float(string="Produit de cession")
    plus_values = fields.Float(
        string="Plus values",
        compute='_compute_plus_moins_values', store=True, readonly=True,
    )
    moins_values = fields.Float(
        string="Moins values",
        compute='_compute_plus_moins_values', store=True, readonly=True,
    )

    @api.depends('montant_brut', 'amort_cumules')
    def _compute_valeur_nette(self):
        """T10!E = "=C-D": the gross value less the amortisation already
        booked on the asset being disposed of."""
        for line in self:
            line.valeur_nette = line.montant_brut - line.amort_cumules

    @api.depends('produit_cession', 'valeur_nette')
    def _compute_plus_moins_values(self):
        """T10!G/H: the disposal result, split over the two columns by its
        sign. Computed together so they can never both be non-zero."""
        for line in self:
            resultat = line.produit_cession - line.valeur_nette
            line.plus_values = max(resultat, 0.0)
            line.moins_values = max(-resultat, 0.0)
