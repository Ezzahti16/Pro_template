# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    def action_create_new_version(self):
        self.ensure_one()
        reason = self.company_id.dumurosol_version_cancel_reason_id
        if not reason:
            raise UserError(_(
                "Aucun motif d'annulation par défaut n'est configuré pour la "
                "création de nouvelles versions de devis. Configurez-le dans "
                "Ventes > Configuration > Paramètres, section « Devis & "
                "Commandes »."
            ))

        result = super().action_create_new_version()
        self._cancel_after_new_version(reason)
        return result

    def _cancel_after_new_version(self, reason):
        self.ensure_one()
        # action_cancel() refuses to cancel a locked order ("You cannot
        # cancel a locked order. Please unlock it first."). Since this
        # cancellation is the automated, sanctioned side-effect of creating
        # a new version (not a manual edit), unlock first if needed.
        if self.locked:
            self.action_unlock()
        self.write({
            'cancel_reason_id': reason.id,
            'cancel_reason_note': _("Devis annulé automatiquement suite à la "
                                     "création d'une nouvelle version."),
        })
        self.message_post(body=_(
            "Devis annulé automatiquement suite à la création d'une "
            "nouvelle version. Motif : %s"
        ) % reason.name)
        self.action_cancel()
