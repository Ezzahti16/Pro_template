# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .account_report import _ma_fiscal_year_selection


class MaFiscalDotationLine(models.Model):
    """One row of the "État des Dotations aux Amortissements Relatifs aux
    Immobilisations" report, for one company. Same concept as the other
    ma.fiscal.*.line models (see AccountReportHandlerMaDotation /
    AccountReportHandlerMaDataEntry in models/account_report.py).

    taux/duree are both plain, freely editable fields - the popup keeps
    them in sync live in either direction (taux = 100/duree, or duree =
    100/taux - see the pair_field/pair_formula field-spec keys and
    MA_PAIR_FNS in data_entry_popover.js), so whichever one the user last
    touched is already consistent with the other by the time this model
    ever sees them; nothing more to enforce here.

    amort_normal/depassement_plafond/total_amort stay read-only and are
    recomputed below on every create/write, mirroring EliteIS's own live
    popup calculation:
      - amort_normal = prix_acquisition - amort_anterieurs, forced to 0
        (and flagged via depassement_plafond, which the report renders as
        a blank cell) whenever that would exceed the legal cap
        valeur_comptable / durée
      - total_amort = amort_anterieurs + amort_normal (= "col.4 + col.7"
        per the legacy sheet's own column-9 header)
    """
    _name = 'ma.fiscal.dotation.line'
    _description = "Ligne d'immobilisation - État des Dotations aux Amortissements"
    _order = 'sequence, id'
    _rec_name = 'immobilisation'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    immobilisation = fields.Char(string="Immobilisations concernées")
    date_entree = fields.Date(string="Date d'entrée")
    prix_acquisition = fields.Float(string="Valeur à amortir - Prix d'acquisition")
    valeur_comptable = fields.Float(string="Valeur à amortir - Valeur comptable après réévaluation")
    amort_anterieurs = fields.Float(string="Amortissements antérieurs")
    taux = fields.Float(string="Taux")
    duree = fields.Integer(string="Durée")
    amort_normal = fields.Float(string="Amortissements normaux ou accélérés de l'exercice", readonly=True)
    depassement_plafond = fields.Boolean(string="Dépasse le plafond légal", readonly=True)
    total_amort = fields.Float(string="Total des amortissements à la fin de l'exercice", readonly=True)
    observations = fields.Char(string="Observations")

    @staticmethod
    def _ma_compute_amort_values(prix_acquisition, amort_anterieurs, valeur_comptable, duree):
        amort_normal_brut = prix_acquisition - amort_anterieurs
        plafond = (valeur_comptable / duree) if duree else 0.0
        depassement = amort_normal_brut > plafond
        amort_normal = 0.0 if depassement else amort_normal_brut
        return {
            'amort_normal': amort_normal,
            'depassement_plafond': depassement,
            'total_amort': amort_anterieurs + amort_normal,
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals.update(self._ma_compute_amort_values(
                vals.get('prix_acquisition') or 0.0,
                vals.get('amort_anterieurs') or 0.0,
                vals.get('valeur_comptable') or 0.0,
                vals.get('duree') or 0,
            ))
        return super().create(vals_list)

    def write(self, vals):
        if any(key in vals for key in ('prix_acquisition', 'amort_anterieurs', 'valeur_comptable', 'duree')):
            for record in self:
                record_vals = dict(vals)
                record_vals.update(self._ma_compute_amort_values(
                    vals.get('prix_acquisition', record.prix_acquisition) or 0.0,
                    vals.get('amort_anterieurs', record.amort_anterieurs) or 0.0,
                    vals.get('valeur_comptable', record.valeur_comptable) or 0.0,
                    vals.get('duree', record.duree) or 0,
                ))
                super(MaFiscalDotationLine, record).write(record_vals)
            return True
        return super().write(vals)
