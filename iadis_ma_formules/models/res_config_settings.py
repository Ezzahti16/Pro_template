# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ma_formules_register_secret = fields.Char(
        string="Secret d'enregistrement",
        config_parameter='ma_formules.register_secret',
        help="Secret partagé que toute installation dbc_liasse_fiscale doit "
             "envoyer (en-tête X-Register-Secret) pour s'enregistrer ou "
             "modifier une formule via l'API.",
    )
