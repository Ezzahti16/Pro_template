# -*- coding: utf-8 -*-
from odoo import fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalLeasingLine(models.Model):
    """One row of the "Tableau des Biens en Crédit-Bail" report, for one
    company. Same concept as ma.fiscal.capital.line / ma.fiscal.loan.line:
    no EDI code or formula behind any field, just plain values the user
    keys in through the report's own header "+" button and per-row
    pencil/trash icons (see AccountReportHandlerMaLeasing /
    action_ma_data_entry_save in models/account_report.py).
    """
    _name = 'ma.fiscal.leasing.line'
    _description = "Ligne de bien en crédit-bail - Tableau des Biens en Crédit-Bail"
    _order = 'sequence, id'
    _rec_name = 'rubrique'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    rubrique = fields.Char(string="Rubriques")
    date_premiere_echeance = fields.Date(string="Date de la 1ère échéance")
    duree_contrat_mois = fields.Integer(string="Durée du contrat (mois)")
    valeur_estimee = fields.Float(string="Valeur estimée du bien à la date du contrat")
    duree_theorique_amortissement = fields.Float(string="Durée théorique d'amortissement du bien")
    cumul_redevances_anterieures = fields.Float(string="Cumul des redevances des exercices précédents")
    montant_redevances_exercice = fields.Float(string="Montant des redevances de l'exercice")
    redevances_restant_moins_un_an = fields.Float(string="Redevances restant à payer à moins d'un an")
    redevances_restant_plus_un_an = fields.Float(string="Redevances restant à payer à plus d'un an")
    prix_achat_residuel = fields.Float(string="Prix d'achat résiduel en fin de contrat")
    observations = fields.Char(string="Observations")
