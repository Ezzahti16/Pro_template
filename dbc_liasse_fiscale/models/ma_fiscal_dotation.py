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

    The two amortisation columns are treated differently, because only one
    of them has a rule:

    * amort_normal is EDITABLE. It used to be recomputed on every
      create/write and forced to 0 whenever prix_acquisition -
      amort_anterieurs exceeded the annual cap valeur_comptable / durée -
      which is almost always, since that difference is the whole remaining
      value of the asset while the cap is one year of it, so the column came
      out 0 on every asset not written off in a single year. It is now only
      DERIVED WHEN A ROW IS CREATED WITHOUT IT, as
      min(prix_acquisition - amort_anterieurs, valeur_comptable / durée),
      never negative - the annuity, capped by what is left to amortise.
      Whatever the accountant types wins, because no rule reproduces a real
      état: the reference liasse prorates assets acquired during the year,
      and not uniformly (one gets exactly half a year, another ~0.71,
      another ~0.27 - none matching its own acquisition date).

    * total_amort is COMPUTED and stored: amort_anterieurs + amort_normal,
      which is the DGI sheet's own column-9 header ("col.4 + col.7") and
      holds on every row of the reference liasse. Nothing to key in, and it
      can never drift from the two columns it adds up.
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
    amort_normal = fields.Float(string="Amortissements normaux ou accélérés de l'exercice")
    total_amort = fields.Float(
        string="Total des amortissements à la fin de l'exercice",
        compute='_compute_total_amort', store=True, readonly=True,
    )
    observations = fields.Char(string="Observations")

    @api.depends('amort_anterieurs', 'amort_normal')
    def _compute_total_amort(self):
        """Column 8 of the DGI sheet, whose own header reads "col.4 + col.7"."""
        for line in self:
            line.total_amort = line.amort_anterieurs + line.amort_normal

    @staticmethod
    def _ma_default_amort_normal(prix_acquisition, amort_anterieurs, valeur_comptable, duree):
        """Default annuity for a row created without one: one year of
        amortisation (valeur_comptable / durée), capped by what is left to
        amortise (prix_acquisition - amort_anterieurs) and never negative."""
        reste = prix_acquisition - amort_anterieurs
        annuite = (valeur_comptable / duree) if duree else 0.0
        return max(0.0, min(reste, annuite))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Only fill what the caller left out - a typed value always wins,
            # including a deliberate 0. Nothing is recomputed on write, so an
            # edit to another column never silently rewrites amort_normal.
            vals.setdefault('amort_normal', self._ma_default_amort_normal(
                vals.get('prix_acquisition') or 0.0,
                vals.get('amort_anterieurs') or 0.0,
                vals.get('valeur_comptable') or 0.0,
                vals.get('duree') or 0,
            ))
        return super().create(vals_list)
