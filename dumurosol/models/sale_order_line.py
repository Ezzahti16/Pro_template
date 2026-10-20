from odoo import api, models, _
from odoo.exceptions import UserError

from .sale_order import dumurosol_field_label

# Champs supplémentaires (au-delà de la liste native
# _get_protected_fields()) bloqués sur une ligne de devis/commande
# confirmée ou envoyée-verrouillée. qty_delivered n'est PAS protégé pour
# les produits de type Service : la quantité livrée y est un champ de
# saisie manuelle (ex: facturation au forfait/à l'étape), que l'utilisateur
# doit pouvoir renseigner après confirmation.
EXTRA_PROTECTED_FIELDS = {'qty_delivered'}


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _is_protected_by_dumurosol(self):
        self.ensure_one()
        return self.order_id._dumurosol_is_write_protected()

    def _dumurosol_protected_fields(self):
        """Champs protégés pour CETTE ligne. qty_delivered est exclu pour
        les produits de type Service, qui restent modifiables même sur une
        ligne par ailleurs protégée."""
        self.ensure_one()
        protected = set(self._get_protected_fields())
        if self.product_id.type != 'service':
            protected |= EXTRA_PROTECTED_FIELDS
        return protected

    def write(self, vals):
        if not self.env.su:
            touched_fields = set(vals.keys())
            blocked_lines = self.env['sale.order.line']
            blocked_fields = set()
            for line in self:
                if not line._is_protected_by_dumurosol():
                    continue
                hit = line._dumurosol_protected_fields() & touched_fields
                if hit:
                    blocked_lines |= line
                    blocked_fields |= hit
            if blocked_lines:
                field_labels = [
                    dumurosol_field_label(blocked_lines, fname)
                    for fname in blocked_fields
                ]
                raise UserError(_(
                    "Il est interdit de modifier les champs suivants sur une "
                    "ligne de ce devis/cette commande :\n%s",
                    '\n'.join(field_labels),
                ))
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            orders_cache = {}
            for vals in vals_list:
                # Down-payment lines (and their section header) are created
                # directly on confirmed orders by core's invoicing flow —
                # always flagged is_downpayment=True; never user-initiated.
                if vals.get('is_downpayment'):
                    continue
                order_id = vals.get('order_id')
                if not order_id:
                    continue
                order = orders_cache.get(order_id)
                if order is None:
                    order = orders_cache[order_id] = self.env['sale.order'].browse(order_id)
                if order._dumurosol_is_write_protected():
                    raise UserError(_(
                        "Il est interdit d'ajouter une ligne à ce devis/cette "
                        "commande (%s)."
                    ) % order.name)
        return super().create(vals_list)

    def unlink(self):
        if not self.env.su:
            blocked = self.filtered(
                lambda l: not l.is_downpayment and l._is_protected_by_dumurosol())
            if blocked:
                raise UserError(_(
                    "Il est interdit de supprimer une ligne de ce devis/cette "
                    "commande (%s)."
                ) % ", ".join(blocked.order_id.mapped('name')))
        return super().unlink()
