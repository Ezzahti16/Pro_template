# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ma_identifiant_fiscal = fields.Char(
        string="Identifiant Fiscal",
        help="Identifiant Fiscal (IF) delivered by the DGI. Written as-is into "
             "the <identifiantFiscal> tag of the EDI liasse file; Simpl-IS "
             "rejects the upload outright when it does not match the declaring "
             "taxpayer, so it is kept separate from the Tax ID / ICE fields.",
    )
