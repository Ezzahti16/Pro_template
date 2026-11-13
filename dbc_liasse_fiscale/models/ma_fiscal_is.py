# -*- coding: utf-8 -*-
from odoo import fields, models

from .account_report import MA_IS_SECTIONS, _ma_fiscal_year_selection


class MaFiscalIsLine(models.Model):
    """One detail row of the two free-text blocks of Tableau n°15, "État pour
    le Calcul de l'Impôt sur les Sociétés", for one company and one fiscal
    year.

    Same concept as ma.fiscal.passage.line: the DGI grid gives these two
    blocks a *libellé* cell and a *montant* cell that are ranges rather than
    single cells, so the taxpayer types both the wording and the amount -

        C_14990 / C_14353  'T15'!$A$13:$A$14 / $B$13:$B$14   (taux réduit)
        C_14355 / C_14356  'T15'!$A$20:$A$21 / $B$20:$B$21   (autres produits)

    and the subtotal rows the paper form draws above them ("3- CA SOUMIS AU
    TAUX REDUIT", "- Autres produits taxables (ajout)") carry no codeEdi at
    all - they are the sum of whatever was typed.

    That is why there is no fixed "CA soumis au taux réduit de 10%" line to
    add: the rate is part of the wording the preparer enters, which is how a
    real liasse ends up showing 8,75%, 10% and a barème plafonné on three
    separate lines.

    Deliberately NO row-count constraint, unlike ma.fiscal.passage.line: the
    grid shows two rows per block, but the reference liasse this was checked
    against uses three in the taux-réduit block, so capping at two would
    refuse data a real filing contains. See the note in the EDI exporter -
    the limit needs confirming against a filed IS tableau before it is
    enforced anywhere.
    """
    _name = 'ma.fiscal.is.line'
    _description = "Ligne de détail - État pour le Calcul de l'Impôt sur les Sociétés"
    _order = 'section, sequence, id'
    _rec_name = 'intitule'

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company,
    )
    sequence = fields.Integer(default=10)
    annee_fiscale = fields.Selection(
        selection=lambda self: _ma_fiscal_year_selection(), string="Année fiscale", required=True,
        default=lambda self: str(fields.Date.context_today(self).year),
    )
    section = fields.Selection(
        MA_IS_SECTIONS, string="Rubrique", required=True, default='ca_taux_reduit',
    )
    intitule = fields.Char(string="Intitulé")
    montant = fields.Float(string="Montant")
