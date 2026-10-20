# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrderApproveWizard(models.TransientModel):
    _name = 'dumurosol.sale.order.approve.wizard'
    _description = "Assistant de validation d'un devis (remarque optionnelle)"

    sale_order_id = fields.Many2one(
        'sale.order', string="Devis", required=True,
        default=lambda self: self.env.context.get('active_id'))
    remark = fields.Text(string="Remarque (visible par tous, facultative)")

    def action_confirm_approve(self):
        self.ensure_one()
        self.sale_order_id.action_validate_current_level(remark=self.remark)
        return {'type': 'ir.actions.act_window_close'}
