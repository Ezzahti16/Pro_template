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
    ma_raison_sociale = fields.Char(
        string="Raison sociale",
        help="Legal name as it is to appear on the cover page of the liasse. "
             "Kept apart from the company name because the name Odoo is run "
             "under is often a trading or short name, while the cover page of "
             "a tax return has to carry the registered one. Left empty, the "
             "company name is used.",
    )

    # ------------------------------------------------------------------ liasse

    def _dbc_liasse_raison_sociale(self):
        """What the cover page prints as the raison sociale."""
        self.ensure_one()
        return (self.ma_raison_sociale or '').strip() or self.display_name

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
