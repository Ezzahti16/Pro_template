from odoo import fields, models, _
from odoo.exceptions import UserError

# Once a sale order is confirmed (state == 'sale'), NO field may be written
# through the normal write() path, except this small, stable set of
# technical/workflow fields that Odoo's own native features (or this
# module's own controlled flows) must still be able to touch. This is
# deny-by-default: any business field added later (by this module or any
# other) is blocked automatically, with nothing to maintain here.
ALWAYS_ALLOWED_FIELDS = {
    'state',               # native state transitions (e.g. action_cancel -> 'cancel')
    'locked',              # native "Lock"/"Unlock" buttons (Lock Confirmed Orders feature)
    'cancel_reason_id',    # set by our own cancel wizard just before actual cancellation
    'cancel_reason_note',
    # Internal tracking fields of the quotation-validation workflow
    # (sale_order_validation.py). A 'sent' quotation can be locked (see
    # write() below) while its confirmation/validation circuit is still
    # being driven by that workflow's own code — these writes must go
    # through regardless, since they are system-managed state, not user
    # edits to the quotation's content.
    'current_validation_rule_id',
    'current_validation_level_id',
    'validation_state',
    'validation_level_deadline',
    'validation_rejection_reason',
    'validation_submitted_date',
}

# Fields that are only exceptionally allowed, and only for the exact write
# that transitions a quotation to state == 'sale' (see write() below). Kept
# separate from ALWAYS_ALLOWED_FIELDS so this narrow, transition-scoped
# exception can never be confused with a permanent exemption.
CONFIRMING_TRANSITION_EXTRA_FIELDS = {'date_order'}

# French labels for the fields most likely to show up in the error message.
# Field .string attributes are defined in English by core Odoo and are not
# dynamically translated on plain attribute access (only fields_get()/views
# apply translation), so we provide our own mapping instead of depending on
# whether a fr_FR language pack happens to be installed. Falls back to the
# field's native string (then to the technical name) for anything not
# listed here — deny-by-default means any field could in principle appear.
FR_FIELD_LABELS = {
    'partner_id': "Client",
    'partner_invoice_id': "Adresse de facturation",
    'partner_shipping_id': "Adresse de livraison",
    'payment_term_id': "Conditions de paiement",
    'fiscal_position_id': "Position fiscale",
    'currency_id': "Devise",
    'date_order': "Date de la commande",
    'validity_date': "Date d'expiration",
    'client_order_ref': "Référence client",
    'reference': "Référence",
    'origin': "Origine",
    'tag_ids': "Étiquettes",
    'user_id': "Vendeur",
    'team_id': "Équipe commerciale",
    'medium_id': "Support",
    'source_id': "Source",
    'campaign_id': "Campagne",
    'require_signature': "Signature en ligne",
    'require_payment': "Paiement en ligne",
    'prepayment_percent': "Pourcentage d'acompte",
    'note': "Conditions générales",
    'order_line': "Lignes de commande",
    # sale.order.line fields (reused by sale_order_line.py)
    'product_id': "Produit",
    'name': "Description",
    'price_unit': "Prix unitaire",
    'product_uom_id': "Unité de mesure",
    'product_uom_qty': "Quantité",
    'tax_ids': "Taxes",
    'analytic_distribution': "Distribution analytique",
    'discount': "Remise (%)",
    'qty_delivered': "Quantité livrée",
}


def dumurosol_field_label(model, fname):
    if fname in FR_FIELD_LABELS:
        return FR_FIELD_LABELS[fname]
    if fname in model._fields:
        return model._fields[fname].string
    return fname


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    cancel_reason_id = fields.Many2one(
        'dumurosol.sale.order.cancel.reason',
        string="Motif d'annulation",
        readonly=True,
        copy=False,
        tracking=True,
    )
    cancel_reason_note = fields.Text(
        string="Détails de l'annulation",
        readonly=True,
        copy=False,
    )

    def _lock_confirmed_orders_setting_active(self):
        return self.env['res.groups']._is_feature_enabled('sale.group_auto_done_setting')

    def _dumurosol_is_write_protected(self):
        """True if this order's content must not be modified at all.

        Shared by this header-level guard and sale_order_line.py's own
        write/create/unlink guards, so both enforce the exact same policy.
        """
        self.ensure_one()
        if self.state in ('sale', 'cancel'):
            return True
        if self.state == 'sent' and self.locked:
            return self._lock_confirmed_orders_setting_active()
        return False

    def write(self, vals):
        # Mirror the native "auto-lock on confirm" behavior for the "Envoyé"
        # (sent) stage: if the "Lock Confirmed Orders" setting is active,
        # sending a quotation locks it the same way confirming one does.
        if vals.get('state') == 'sent' and 'locked' not in vals \
                and self._lock_confirmed_orders_setting_active():
            vals = dict(vals, locked=True)

        if not self.env.su:
            blocked_orders = self.filtered(lambda o: o._dumurosol_is_write_protected())
            if blocked_orders:
                # The write that actually confirms a quotation (state ->
                # 'sale') bundles 'date_order' in the same call (see core's
                # sale.order._prepare_confirmation_values()). A locked
                # 'sent' quotation is locked to prevent *content* edits — it
                # must not prevent the quotation from being confirmed in the
                # first place. So exactly these two fields are allowed when,
                # and only when, this call is that confirming transition.
                # Any other field bundled into the same call is still
                # blocked, so this can't be used to sneak in other changes.
                allowed = set(ALWAYS_ALLOWED_FIELDS) | {'order_line'}
                if vals.get('state') == 'sale':
                    allowed |= CONFIRMING_TRANSITION_EXTRA_FIELDS

                # 'order_line' is always allowed here: editing a line
                # through the order's own write() (e.g. the web client
                # saving an inline edit as
                # order.write({'order_line': [(1, line_id, {...})]})) is
                # fully governed by sale.order.line's own write/create/unlink
                # guards (sale_order_line.py), which know the field- and
                # product-specific exceptions (e.g. qty_delivered on
                # services). Blocking it here too would pre-empt those
                # exceptions before the line-level guard ever runs.
                blocked_fields = set(vals.keys()) - allowed
                if blocked_fields:
                    field_labels = [
                        dumurosol_field_label(blocked_orders, fname)
                        for fname in blocked_fields
                    ]
                    raise UserError(_(
                        "Il est interdit de modifier ce devis/cette commande "
                        "(champs concernés : %s).",
                        ', '.join(field_labels),
                    ))

        # sale_quotation_versioning locks any non-draft order unconditionally
        # (its own, independent rule). Our rule above is now the single
        # source of truth for this policy (in particular: a 'sent' quotation
        # must NOT be blocked when "Lock Confirmed Orders" is inactive), so
        # once we've decided to let a write through, bypass that module's
        # own check rather than have it re-block what we just allowed.
        return super(SaleOrder, self.with_context(
            bypass_quotation_version_lock=True)).write(vals)

    def action_cancel_with_reason(self):
        """Open the cancellation wizard requiring a mandatory reason."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Annuler le devis"),
            'res_model': 'dumurosol.sale.order.cancel.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }
