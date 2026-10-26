# -*- coding: utf-8 -*-
from odoo import fields, models


class MaFiscalFormulaLog(models.Model):
    _name = 'ma.fiscal.formula.log'
    _description = 'Moroccan Fiscal Formula Edit Log'
    _order = 'create_date desc'
    _rec_name = 'edi_code'

    edi_code = fields.Char(string="EDI Code", required=True, readonly=True)
    year = fields.Integer(string="Fiscal Year", required=True, readonly=True)
    client_id = fields.Char(string="Client ID", readonly=True)
    old_formula = fields.Char(string="Old Formula", readonly=True)
    new_formula = fields.Char(string="New Formula", readonly=True)
    user_id = fields.Many2one(
        'res.users', string="Modified By", required=True, readonly=True,
        default=lambda self: self.env.user,
    )
