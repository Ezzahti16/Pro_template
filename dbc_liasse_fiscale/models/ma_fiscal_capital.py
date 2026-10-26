# -*- coding: utf-8 -*-
from odoo import fields, models


class MaFiscalCapitalLine(models.Model):
    """One associate's row of the "État de Répartition du Capital Social"
    report, for one company. Unlike the EDI-code based bilan_* reports,
    this statement has no formula - and no date/fiscal-year scoping -
    behind any of its values: every field is a plain value the user keys
    in directly, through the report's own add/edit/delete popups (see
    AccountReportHandlerMaCapital / action_modify_capital_line in
    models/account_report.py). The "previous"/"current" exercise title
    counts are just two plain columns on the same row (titres_precedent /
    titres_actuel below), not two separate year-scoped records.
    """
    _name = 'ma.fiscal.capital.line'
    _description = "Ligne d'associé - Répartition du Capital Social"
    _order = 'sequence, id'
    _rec_name = 'nom_prenom'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    nom_prenom = fields.Char(string="Nom et prénom")
    raison_sociale = fields.Char(string="Raison sociale")
    num_if = fields.Char(string="N° IF")
    num_cin = fields.Char(string="N° CIN")
    num_ce = fields.Char(string="N° Carte d'étranger")
    adresse = fields.Char(string="Adresse")
    titres_precedent = fields.Integer(string="Nombre de titres (exercice précédent)")
    titres_actuel = fields.Integer(string="Nombre de titres (exercice actuel)")
    valeur_nominale = fields.Float(string="Valeur nominale")
    montant_souscrit = fields.Float(string="Montant du capital - Souscrit")
    montant_appele = fields.Float(string="Montant du capital - Appelé")
    montant_libere = fields.Float(string="Montant du capital - Libéré")
