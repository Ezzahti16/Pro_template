# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import UserError


class SaleOrderRejectWizard(models.TransientModel):
    _name = 'dumurosol.sale.order.reject.wizard'
    _description = "Assistant de rejet d'un devis (motif obligatoire)"

    sale_order_id = fields.Many2one(
        'sale.order', string="Devis", required=True,
        default=lambda self: self.env.context.get('active_id'))
    reason = fields.Text(string="Motif de rejet", required=True)

    def action_confirm_reject(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise UserError(_("Le motif de rejet est obligatoire."))
        self.sale_order_id.action_reject_current_level(reason=self.reason)
        return {'type': 'ir.actions.act_window_close'}
