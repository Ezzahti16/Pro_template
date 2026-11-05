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
    year = fields.Integer(string="Fiscal Year", aggregator=False)

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
    def _update_formula(self, client_id, year, edi, formule):
        """Set the formula of one EDI code for one client's one fiscal year.

        Self-healing: if that exact (client_id, year, edi) row doesn't
        exist yet (e.g. a code added to the master set after this client's
        year was cloned, or the client hasn't re-registered since a new
        year rolled around), it is created from the master row's other
        fields instead of failing. Raises if even the master row for that
        edi is unknown.

        Returns the row's new `formule`.
        """
        row = self.search([
            ('client_id', '=', client_id), ('year', '=', year), ('edi', '=', edi),
        ], limit=1)
        if row:
            row.formule = formule
            return row.formule

        master_row = self.search([
            ('client_id', '=', False), ('year', '=', False), ('edi', '=', edi),
        ], limit=1)
        if not master_row:
            raise ValueError("Unknown EDI code: %s" % edi)

        new_row = self.create({
            'edi': edi,
            'table_': master_row.table_,
            'formule': formule,
            'modele': master_row.modele,
            'code_comptable': master_row.code_comptable,
            'libelle': master_row.libelle,
            'client_id': client_id,
            'year': year,
        })
        return new_row.formule

    @api.model
    def _import_master_csv(self):
        """Create any master EDI code from data/ma.fiscal.setting.csv that
        doesn't already exist yet, without ever touching an existing row's
        fields.

        The CSV is reloaded on every `-u` (it's in __manifest__.py's `data`
        list), but the `pre-freeze_master_formulas` migration marks every
        already-imported row `noupdate=True` before that reload happens, so
        the reload only ever creates rows for brand-new xmlids — it never
        overwrites a formula someone edited by hand. This method is a plain
        manual helper on top of that: safe to call anytime (e.g. from a
        shell) to pick up new EDI codes added to the CSV, it never touches
        an existing row's fields either.

        Returns the number of rows created.
        """
        import csv
        import os
        from odoo.modules.module import get_module_path

        module_path = get_module_path('dbc_liasse_fiscal_formules')
        if not module_path:
            return 0
        csv_path = os.path.join(module_path, 'data', 'ma.fiscal.setting.csv')
        if not os.path.exists(csv_path):
            return 0

        existing_edis = set(self.search(
            [('client_id', '=', False), ('year', '=', False)]).mapped('edi'))
        to_create = []
        with open(csv_path, encoding='utf-8') as f:
            for row in csv.DictReader(f):
                edi = row['edi']
                if edi in existing_edis:
                    continue
                to_create.append({
                    'edi': edi,
                    'table_': int(row['table_']) if row['table_'] else 0,
                    'formule': row['formule'],
                    'modele': int(row['modele']) if row['modele'] else 0,
                    'code_comptable': row['code_comptable'] or False,
                    'libelle': row['libelle'] or False,
                })
                existing_edis.add(edi)
        if to_create:
            self.create(to_create)
        return len(to_create)

    @api.model
    def _clone_year_for_client(self, client, year):
        """Create `year`'s rows for `client`, copying them from the closest
        PRIOR year this client already has rows for — not the master set —
        so a formula edited for one year carries forward into the next
        instead of being reset to the template. Only the very first year a
        client ever gets (no prior client year exists yet) falls back to
        the master rows.

        No-op if `year` already has rows for this client (idempotent, same
        as the old per-year check this replaces).
        """
        if self.search_count([('client_id', '=', client.id), ('year', '=', year)]):
            return

        prior = self.search([
            ('client_id', '=', client.id), ('year', '<', year),
        ], order='year desc', limit=1)
        if prior:
            source_rows = self.search([
                ('client_id', '=', client.id), ('year', '=', prior.year),
            ])
        else:
            source_rows = self.search([('client_id', '=', False), ('year', '=', False)])
        if not source_rows:
            return

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
            for row in source_rows
        ])

    @api.model
    def _clone_master_for_client(self, client):
        """Set up `client` for the first time: one row set per fiscal year
        from _CLONE_START_YEAR up to and including the current year.

        Each year is built by `_clone_year_for_client`, which chains off
        the previous year once one exists — only _CLONE_START_YEAR itself
        has no prior client year, so that one alone comes from the master
        set. Idempotent per year (see `_clone_year_for_client`), so calling
        this again after a partial failure only fills in missing years.
        """
        current_year = date.today().year
        for year in range(_CLONE_START_YEAR, current_year + 1):
            self._clone_year_for_client(client, year)

    @api.model
    def _cron_fill_new_year(self):
        """Yearly cron: give every already-registered client this year's
        formula rows, cloned from their own most recent prior year (never
        the master set) so edits they made carry forward automatically.

        Runs monthly rather than exactly once a year on purpose: each call
        is a no-op for a client/year that already exists, so a missed or
        delayed firing (e.g. the server was down on Jan 1st) just gets
        caught on the next run instead of silently skipping that client.
        """
        current_year = date.today().year
        for client in self.env['ma.fiscal.client'].search([]):
            self._clone_year_for_client(client, current_year)
