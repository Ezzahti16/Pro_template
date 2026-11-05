# -*- coding: utf-8 -*-
from odoo import fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalLoanLine(models.Model):
    """One row of the "État des Emprunts et Dettes Assimilées" report, for
    one company. Same concept as ma.fiscal.capital.line: no EDI code or
    formula behind any field, just plain values the user keys in through
    the report's own header "+" button and per-row pencil/trash icons
    (see AccountReportHandlerMaLoan / action_ma_data_entry_save in
    models/account_report.py).
    """
    _name = 'ma.fiscal.loan.line'
    _description = "Ligne d'emprunt - État des Emprunts et Dettes Assimilées"
    _order = 'sequence, id'
    _rec_name = 'nom_prenom'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    type = fields.Selection(
        [('associe', "A. Associés"), ('tiers', "B. Tiers")],
        string="Type", required=True, default='associe',
    )
    nom_prenom = fields.Char(string="Nom et prénom")
    raison_sociale = fields.Char(string="Raison sociale")
    adresse = fields.Char(string="Adresse")
    num_if = fields.Char(string="IF")
    num_cin = fields.Char(string="N° CNI")
    montant_pret = fields.Float(string="Montant du prêt")
    date_pret = fields.Date(string="Date du prêt")
    duree_mois = fields.Integer(string="Durée du prêt (en mois)")
    taux_interet = fields.Float(string="Taux d'intérêts")
    charge_financiere_globale = fields.Float(string="Charge financière globale")
    remb_anterieur_principal = fields.Float(string="Remb. Ex antérieurs Principal")
    remb_anterieur_interet = fields.Float(string="Remb. Ex antérieurs Intérêt")
    remb_actuel_principal = fields.Float(string="Remb. Ex actuel Principal")
    remb_actuel_interet = fields.Float(string="Remb. Ex actuel Intérêt")
    observations = fields.Char(string="Observations")
