# -*- coding: utf-8 -*-
from odoo import fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalRentalLine(models.Model):
    """One row of the "Tableau des Locations et Baux autres que le
    Crédit-Bail" report, for one company. Same concept as
    ma.fiscal.capital.line / ma.fiscal.loan.line / ma.fiscal.leasing.line:
    no EDI code or formula behind any field, just plain values the user
    keys in through the report's own header "+" button and per-row
    pencil/trash icons (see AccountReportHandlerMaRental /
    action_ma_data_entry_save in models/account_report.py).
    """
    _name = 'ma.fiscal.rental.line'
    _description = "Ligne de location - Tableau des Locations et Baux autres que le Crédit-Bail"
    _order = 'sequence, id'
    _rec_name = 'nature_bien'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    nature_bien = fields.Char(string="Nature du bien loué")
    lieu_situation = fields.Char(string="Lieu de situation")
    nom_prenom = fields.Char(string="Nom et prénoms du propriétaire")
    raison_sociale = fields.Char(string="Raison sociale du propriétaire")
    adresse = fields.Char(string="Adresse du propriétaire")
    num_if = fields.Char(string="N° IF du propriétaire")
    num_cin = fields.Char(string="N° CNI du propriétaire")
    num_ce = fields.Char(string="N° Carte d'étranger du propriétaire")
    date_conclusion = fields.Date(string="Date de conclusion de l'acte de location")
    montant_annuel = fields.Float(string="Montant annuel de location")
    montant_loyer_charges = fields.Float(string="Montant du loyer compris dans les charges de l'exercice")
    # Legacy sheet marks whichever applies with an "X" rather than picking
    # one of two options - two plain text columns, not a selection field.
    bail_ordinaire = fields.Char(string="Nature du contrat : Bail-ordinaire")
    leasing_nieme_periode = fields.Char(string="Nature du contrat : Leasing (Nème période)")
