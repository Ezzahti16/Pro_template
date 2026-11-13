# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    ma_date_decision_affectation = fields.Date(
        string="Date de décision d'affectation",
        help="Date of the general meeting that decided how the result is "
             "allocated. The Tableau d'Affectation des Résultats prints it as "
             "\"Décision du ...\". The liasse defines no EDI code for it - the "
             "DGI workbook's Tableau 14 declares only its thirteen amounts - "
             "so it cannot be derived from the accounts and is kept here.",
    )
