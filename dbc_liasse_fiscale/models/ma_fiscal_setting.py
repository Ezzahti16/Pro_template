# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.tools import ormcache


class MaFiscalSetting(models.Model):
    _name = 'ma.fiscal.setting'
    _description = 'Moroccan Fiscal Setting (EDI code)'
    _order = 'table_, id'

    edi = fields.Char(string="EDI Code", required=True, index=True)
    table_ = fields.Integer(string="Table")
    formule = fields.Text(string="Formule")
    modele = fields.Integer(string="Modele")
    code_comptable = fields.Char(string="Code Comptable")
    libelle = fields.Char(string="Libellé")

    _sql_constraints = [
        ('edi_uniq', 'unique(edi)', 'The EDI code must be unique.'),
    ]

    @api.model
    def _get_formules(self):
        """ Returns {'<edi_code>': '<formule>'} for every setting record. """
        return {r.edi: r.formule or '0' for r in self.search([])}
