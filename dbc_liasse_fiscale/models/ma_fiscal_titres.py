# -*- coding: utf-8 -*-
from odoo import fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalTitresLine(models.Model):
    """One row of the "Tableau des Titres de Participation" report, for
    one company. Same concept as the other ma.fiscal.*.line models: no
    EDI code or formula behind any field, just plain values the user keys
    in through the report's own header "+" button and per-row pencil/trash
    icons (see AccountReportHandlerMaTitres / action_ma_data_entry_save in
    models/account_report.py).
    """
    _name = 'ma.fiscal.titres.line'
    _description = "Ligne de titre - Tableau des Titres de Participation"
    _order = 'sequence, id'
    _rec_name = 'raison_sociale'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    raison_sociale = fields.Char(string="Raison sociale de la société émettrice")
    num_if = fields.Char(string="N° IF")
    secteur_activite = fields.Char(string="Secteur d'activité")
    capital_social = fields.Float(string="Capital social")
    participation_capital = fields.Float(string="Participation au capital %")
    prix_acquisition_global = fields.Float(string="Prix d'acquisition global")
    valeur_comptable_nette = fields.Float(string="Valeur comptable nette")
    date_cloture = fields.Date(string="Date de clôture")
    situation_nette = fields.Float(string="Situation nette")
    resultat_net = fields.Float(string="Résultat net")
    produits_cpc = fields.Float(string="Produits inscrits au C.P.C de l'exercice")
