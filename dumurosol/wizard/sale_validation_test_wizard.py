# -*- coding: utf-8 -*-
from odoo import fields, models, _


class SaleValidationTestWizard(models.TransientModel):
    _name = 'dumurosol.sale.validation.test.wizard'
    _description = "Assistant de simulation / test de règle de validation des devis"

    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company)
    amount = fields.Monetary(string="Montant HT (fictif)", currency_field='currency_id', required=True)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string="Devise", readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string="Client (fictif)", required=True,
        domain=[('customer_rank', '>', 0)])
    product_ids = fields.Many2many(
        'product.product', string="Produits (fictifs)",
        domain=[('sale_ok', '=', True)])
    max_discount = fields.Float(string="Remise maximale (%, fictive)")

    result_rule_id = fields.Many2one(
        'dumurosol.sale.validation.rule', string="Règle applicable", readonly=True)
    result_summary = fields.Html(string="Résultat de la simulation", readonly=True)

    def action_run_simulation(self):
        self.ensure_one()
        rule = self.env['dumurosol.sale.validation.rule']._find_applicable_rule(
            self.amount, self.partner_id, self.product_ids, self.company_id, self.max_discount)

        if not rule:
            summary = "<p style='color: #a94442;'><strong>%s</strong></p>" % _(
                "Aucune règle applicable trouvée, et aucune règle par défaut "
                "n'est configurée. Cette configuration bloquerait la confirmation "
                "d'un devis réel correspondant à ces critères."
            )
            self.write({'result_rule_id': False, 'result_summary': summary})
        else:
            levels = rule.level_ids.sorted('sequence')
            levels_html = "".join([
                "<li>Niveau %s — %s : %s</li>" % (
                    lvl.sequence, lvl.name,
                    ", ".join(lvl._get_current_validators().mapped('name')) or _("aucun validateur configuré")
                ) for lvl in levels
            ])
            fallback_note = ""
            if rule.is_default_rule:
                fallback_note = "<p><em>%s</em></p>" % _(
                    "Il s'agit de la règle par défaut (fallback) : aucune règle "
                    "spécifique ne correspond à ces critères.")
            summary = (
                "<p><strong>%(label)s</strong> %(rule)s</p>"
                "%(fallback)s"
                "<p>%(circuit_label)s</p>"
                "<ul>%(levels)s</ul>"
            ) % {
                'label': _("Règle applicable :"),
                'rule': rule.name,
                'fallback': fallback_note,
                'circuit_label': _("Circuit de validation résultant :"),
                'levels': levels_html or "<li>%s</li>" % _("Aucun niveau défini."),
            }
            self.write({'result_rule_id': rule.id, 'result_summary': summary})

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'dumurosol.sale.validation.test.wizard',
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }
