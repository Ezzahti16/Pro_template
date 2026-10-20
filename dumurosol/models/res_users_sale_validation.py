# -*- coding: utf-8 -*-
from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    sale_validation_pending_count = fields.Integer(
        string="Devis à valider", compute='_compute_sale_validation_pending_count')

    def _compute_sale_validation_pending_count(self):
        SaleOrder = self.env['sale.order']
        pending = SaleOrder.search([('validation_state', '=', 'pending')])
        for user in self:
            count = len(pending.filtered(
                lambda o: o.is_current_user_validator and user == self.env.user))
            user.sale_validation_pending_count = count if user == self.env.user else 0
