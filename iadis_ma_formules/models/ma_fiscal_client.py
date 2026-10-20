# -*- coding: utf-8 -*-
from odoo import fields, models


class MaFiscalClient(models.Model):
    _name = 'ma.fiscal.client'
    _description = 'Client of a dbc_liasse_fiscale installation'
    _order = 'name'

    name = fields.Char(string="Company Name", required=True, index=True)

    _sql_constraints = [
        ('name_uniq', 'unique(name)', 'A client with this name is already registered.'),
    ]
