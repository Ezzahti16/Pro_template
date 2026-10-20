# -*- coding: utf-8 -*-
from odoo import models, _
from odoo.exceptions import UserError

# "Factures" au sens métier : factures et avoirs, clients et fournisseurs.
# Les écritures comptables diverses ('entry') ne sont pas concernées par
# cette règle (elles restent gérées selon les mécanismes comptables
# standards d'Odoo : hash/verrouillage, droits Comptabilité, etc.).
INVOICE_MOVE_TYPES = ('out_invoice', 'out_refund', 'in_invoice', 'in_refund')

BYPASS_CONTEXT_KEY = 'force_dumurosol_invoice_edit'


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _compute_show_reset_to_draft_button(self):
        super()._compute_show_reset_to_draft_button()
        for move in self:
            if move.state == 'posted' and move.move_type in INVOICE_MOVE_TYPES:
                move.show_reset_to_draft_button = False

    def button_draft(self):
        protected = self._filtered_confirmed_invoices()
        if protected and not self.env.context.get(BYPASS_CONTEXT_KEY):
            raise UserError(_(
                "Il est interdit de repasser en brouillon (et donc d'annuler ou "
                "de modifier) une facture confirmée : %s.\n\n"
                "Pour corriger une facture validée, utilisez le bouton « Avoir » "
                "(note de crédit) afin de l'annuler comptablement, puis émettez si "
                "besoin une nouvelle facture corrigée."
            ) % ", ".join(protected.mapped('name')))
        return super().button_draft()

    def button_cancel(self):
        protected = self._filtered_confirmed_invoices()
        if protected and not self.env.context.get(BYPASS_CONTEXT_KEY):
            raise UserError(_(
                "Il est interdit d'annuler une facture confirmée : %s.\n\n"
                "Utilisez le bouton « Avoir » (note de crédit) pour l'annuler "
                "comptablement."
            ) % ", ".join(protected.mapped('name')))
        return super().button_cancel()

    def unlink(self):
        protected = self.filtered(
            lambda m: m.move_type in INVOICE_MOVE_TYPES and m.posted_before)
        if protected and not self.env.context.get(BYPASS_CONTEXT_KEY):
            raise UserError(_(
                "Il est interdit de supprimer une facture ayant déjà été "
                "confirmée : %s.\n\n"
                "Utilisez le bouton « Avoir » (note de crédit) pour l'annuler "
                "comptablement plutôt que de la supprimer."
            ) % ", ".join(protected.mapped('name')))
        return super().unlink()

    def _filtered_confirmed_invoices(self):
        return self.filtered(
            lambda m: m.move_type in INVOICE_MOVE_TYPES and m.state == 'posted')
