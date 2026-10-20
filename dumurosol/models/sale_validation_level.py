# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SaleValidationLevel(models.Model):
    _name = 'dumurosol.sale.validation.level'
    _description = "Niveau de circuit de validation des devis"
    _order = 'rule_id, sequence, id'

    rule_id = fields.Many2one(
        'dumurosol.sale.validation.rule', string="Règle parente",
        required=True, ondelete='cascade')
    sequence = fields.Integer(string="Ordre du niveau", default=10, required=True)
    name = fields.Char(string="Libellé du niveau", required=True)

    user_ids = fields.Many2many(
        'res.users', 'dumurosol_sale_validation_level_user_rel',
        'level_id', 'user_id', string="Validateurs nommés (logique OU)")

    sla_hours = fields.Float(
        string="Délai maximal (SLA, en heures)", default=48.0,
        help="Délai avant déclenchement d'une alerte de relance.")
    escalation_hours = fields.Float(
        string="Délai avant escalade (heures)", default=96.0,
        help="Délai total (depuis la soumission au niveau) après lequel, en "
             "l'absence de réponse, la demande est escaladée.")
    escalation_user_id = fields.Many2one(
        'res.users', string="Destinataire de l'escalade",
        help="Utilisateur notifié en cas de dépassement de SLA.")

    @api.constrains('user_ids')
    def _check_validators_defined(self):
        for level in self:
            if not level.user_ids:
                raise ValidationError(_(
                    "Le niveau « %s » doit désigner au moins un validateur."
                ) % level.name)

    @api.constrains('sla_hours', 'escalation_hours')
    def _check_sla_positive(self):
        for level in self:
            if level.sla_hours < 0 or level.escalation_hours < 0:
                raise ValidationError(_(
                    "Les délais SLA et d'escalade doivent être positifs ou nuls."))
            if level.escalation_hours and level.sla_hours and \
                    level.escalation_hours < level.sla_hours:
                raise ValidationError(_(
                    "Le délai d'escalade doit être supérieur ou égal au délai de SLA "
                    "pour le niveau « %s »."
                ) % level.name)

    def _get_current_validators(self):
        self.ensure_one()
        return self.user_ids
