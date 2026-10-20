# -*- coding: utf-8 -*-
from datetime import date

from odoo import api, fields, models
from odoo.tools import ormcache

#: First fiscal year for which a client's formulas get cloned on registration.
_CLONE_START_YEAR = 2020


class MaFiscalSetting(models.Model):
    _name = 'ma.fiscal.setting'
    _description = 'Moroccan Fiscal Setting (EDI formula)'
    _order = 'table_, edi'
    _rec_name = 'name'

    edi = fields.Char(string="EDI Code", required=True, index=True)
    table_ = fields.Integer(string="Table")
    formule = fields.Text(string="Formula")
    modele = fields.Integer(string="Modèle")
    code_comptable = fields.Char(string="Account Code")
    libelle = fields.Char(string="Label")

    # A row with both client_id and year unset is a MASTER/template row —
    # the single, manually-maintained set of current formulas, never read
    # directly by a client's report once that client has been registered.
    # A row with both set is a per-client, per-fiscal-year snapshot cloned
    # from the master set at registration time (see _clone_master_for_client).
    client_id = fields.Many2one('ma.fiscal.client', string="Client")
    year = fields.Integer(string="Fiscal Year")

    name = fields.Char(
        string="Name",
        compute='_compute_name',
        store=True,
        index=True,
    )

    @api.depends('edi')
    def _compute_name(self):
        for rec in self:
            rec.name = 'EDI%s' % (rec.edi or '')

    def init(self):
        # unique(edi) only needs to hold within the master set (client_id
        # and year both NULL) and, separately, within one client's one
        # fiscal year — a plain _sql_constraints unique(edi) would forbid
        # the same edi from existing across different clients/years, which
        # is exactly what per-client/per-year cloning requires. Postgres
        # partial unique indexes express both rules precisely.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ma_fiscal_setting_master_edi_uniq
            ON ma_fiscal_setting (edi)
            WHERE client_id IS NULL AND year IS NULL
        """)
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ma_fiscal_setting_client_year_edi_uniq
            ON ma_fiscal_setting (edi, client_id, year)
            WHERE client_id IS NOT NULL AND year IS NOT NULL
        """)

    @api.model
    def _get_formules(self, client_id=None, year=None):
        """Return {'<edi_code>': '<formule>'} for one scope.

        With no arguments: the master/template rows (client_id and year
        both unset) — today's behaviour, unchanged.
        With both `client_id` and `year` given: that client's snapshot for
        that fiscal year.
        """
        if client_id and year:
            domain = [('client_id', '=', client_id), ('year', '=', year)]
        else:
            domain = [('client_id', '=', False), ('year', '=', False)]
        return {r.edi: r.formule or '0' for r in self.search(domain)}

    @api.model
    def get_formules_dict(self, client_id=None, year=None):
        """ Public helper used by the API: formulas keyed by EDI code. """
        return self._get_formules(client_id=client_id, year=year)

    @api.model
    def _clone_master_for_client(self, client):
        """Clone the master formulas for `client`, once per fiscal year
        from _CLONE_START_YEAR up to and including the current year.

        Idempotent per year: a year that already has rows for this client
        is left untouched (so calling this again after a partial failure
        only fills in the missing years, it never duplicates existing ones).
        """
        master_rows = self.search([('client_id', '=', False), ('year', '=', False)])
        if not master_rows:
            return

        current_year = date.today().year
        for year in range(_CLONE_START_YEAR, current_year + 1):
            already_done = self.search_count([
                ('client_id', '=', client.id),
                ('year', '=', year),
            ])
            if already_done:
                continue
            self.create([
                {
                    'edi': row.edi,
                    'table_': row.table_,
                    'formule': row.formule,
                    'modele': row.modele,
                    'code_comptable': row.code_comptable,
                    'libelle': row.libelle,
                    'client_id': client.id,
                    'year': year,
                }
                for row in master_rows
            ])
