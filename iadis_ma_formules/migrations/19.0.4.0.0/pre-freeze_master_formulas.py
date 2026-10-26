# -*- coding: utf-8 -*-
"""Freeze every ma.fiscal.setting row this module already created (tracked
via ir.model.data from data/ma.fiscal.setting.csv) so Odoo's own end-of-
upgrade cleanup never deletes them again, and so the CSV reload later in
this very upgrade never overwrites a formula someone edited by hand.

Runs once, automatically, as a 'pre' migration - i.e. BEFORE this upgrade's
data files are (re)loaded - which is what makes the timing correct: setting
noupdate=True here, before the CSV load, is what stops that same load from
clobbering any already-restored/edited values back to the CSV's originals.
"""


def migrate(cr, version):
    cr.execute("""
        UPDATE ir_model_data
        SET noupdate = true
        WHERE module = 'iadis_ma_formules'
          AND model = 'ma.fiscal.setting'
          AND noupdate = false
    """)
