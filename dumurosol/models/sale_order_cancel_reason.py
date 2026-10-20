from odoo import fields, models


class SaleOrderCancelReason(models.Model):
    _name = 'dumurosol.sale.order.cancel.reason'
    _description = "Motif d'annulation de devis"
    _order = 'sequence, id'

    name = fields.Char(string="Motif", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    requires_precision = fields.Boolean(
        string="Précision obligatoire",
        help="Si activé, le champ « Précisions » devient obligatoire dans "
             "l'assistant d'annulation lorsque ce motif est sélectionné "
             "(typiquement pour un motif générique comme « Autre »).",
    )
