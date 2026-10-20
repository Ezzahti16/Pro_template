# -*- coding: utf-8 -*-
from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    dumurosol_version_cancel_reason_id = fields.Many2one(
        'dumurosol.sale.order.cancel.reason',
        string="Motif d'annulation (nouvelle version de devis)",
        help="Motif appliqué automatiquement au devis d'origine lorsqu'une "
             "nouvelle version en est créée (module Sale Quotation "
             "Versioning).",
    )

    dumurosol_divers_product_id = fields.Many2one(
        'product.product',
        string="Produit générique « Divers »",
        help="Produit générique/placeholder dont l'utilisation sur une "
             "ligne de devis bloque la confirmation (et dont la description "
             "de ligne est affichée sans le nom du produit sur le rapport "
             "imprimé). Laisser vide pour désactiver ce contrôle.",
    )


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dumurosol_version_cancel_reason_id = fields.Many2one(
        related='company_id.dumurosol_version_cancel_reason_id',
        string="Motif d'annulation (nouvelle version de devis)",
        readonly=False,
    )

    dumurosol_divers_product_id = fields.Many2one(
        related='company_id.dumurosol_divers_product_id',
        string="Produit générique « Divers »",
        readonly=False,
    )
