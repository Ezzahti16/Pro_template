# -*- coding: utf-8 -*-
from odoo import fields, models

CONFIRMED_SEQUENCE_CODE = 'dumurosol.sale.order.confirmed'


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    confirmed_order_number = fields.Char(
        string="Numéro de commande",
        readonly=True,
        copy=False,
        help="Numéro attribué (séquence CMD dédiée) à la première "
             "confirmation de ce devis. Le numéro de devis d'origine "
             "(champ Référence ci-dessus) n'est jamais modifié.")

    def _action_confirm(self):
        # À ce stade, l'état natif Odoo est déjà passé à 'sale' (le write est
        # fait juste avant l'appel à cette méthode dans action_confirm()) :
        # c'est le point d'extension prévu par le framework pour ce type
        # d'effet de bord (cf. docstring de sale.order._action_confirm()).
        res = super()._action_confirm()
        for order in self:
            if not order.confirmed_order_number:
                new_number = self.env['ir.sequence'].with_company(
                    order.company_id).next_by_code(CONFIRMED_SEQUENCE_CODE)
                if new_number:
                    # sudo(): this is a technical/system assignment, not a
                    # user edit, and must go through even though the order is
                    # already state == 'sale' at this point (bypasses the
                    # blanket write-protection in sale_order.py).
                    order.sudo().write({'confirmed_order_number': new_number})
        return res
