# -*- coding: utf-8 -*-
from odoo import api, fields, models


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

    # ------------------------------------------------------------------ liasse
    #
    # La raison sociale et les trois identifiants ne sont pas ici : ils vivent
    # dans l'onglet « Identifiants Fiscaux » de la fiche société, et
    # dbc_liasse_edi/models/res_company.py dit lequel répond à quoi. Ce qui
    # reste ci-dessous est de la mise en forme pour la page de garde, pas de
    # l'identité.

    def _dbc_liasse_adresse(self):
        """The company's address, on one line, as the cover page prints it."""
        self.ensure_one()
        parts = [self.street, self.street2,
                 ' '.join(bit for bit in (self.zip, self.city) if bit)]
        if self.state_id:
            parts.append(self.state_id.name)
        if self.country_id:
            parts.append(self.country_id.name)
        return ', '.join(part.strip() for part in parts if part and part.strip())

    def _dbc_liasse_lieu_date(self, date=None):
        """The line above the signature: where the liasse is signed and when.

        The place is the company's own city and the date is the day the
        document is produced - a liasse is signed on the day it is drawn up,
        and nothing in the accounts can say otherwise.
        """
        self.ensure_one()
        when = date or fields.Date.context_today(self)
        city = (self.city or '').strip()
        return '%s Le %s' % (city, when.strftime('%d/%m/%Y')) if city \
            else 'Le %s' % when.strftime('%d/%m/%Y')
