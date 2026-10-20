from odoo import fields, models, _
from odoo.exceptions import ValidationError


class SaleOrderCancelWizard(models.TransientModel):
    _name = 'dumurosol.sale.order.cancel.wizard'
    _description = "Assistant d'annulation de devis"

    order_id = fields.Many2one('sale.order', string="Devis", required=True)
    cancel_reason_id = fields.Many2one(
        'dumurosol.sale.order.cancel.reason',
        string="Motif d'annulation",
        required=True,
    )
    cancel_reason_requires_precision = fields.Boolean(
        related='cancel_reason_id.requires_precision',
        string="Précision requise",
    )
    note = fields.Text(string="Précisions")

    def action_confirm_cancel(self):
        self.ensure_one()
        if not self.cancel_reason_id:
            raise ValidationError(_("Veuillez sélectionner un motif d'annulation."))
        if self.cancel_reason_id.requires_precision and not (self.note or '').strip():
            raise ValidationError(_(
                "Merci de préciser le motif d'annulation dans le champ "
                "« Précisions » (obligatoire pour le motif « %s »)."
            ) % self.cancel_reason_id.name)

        order = self.order_id
        order.write({
            'cancel_reason_id': self.cancel_reason_id.id,
            'cancel_reason_note': self.note,
        })
        message = _("Devis annulé. Motif : %s", self.cancel_reason_id.name)
        if self.note:
            message += _("<br/>Précisions : %s", self.note)
        order.message_post(body=message)
        return order.action_cancel()
