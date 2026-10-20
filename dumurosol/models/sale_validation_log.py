# -*- coding: utf-8 -*-
from odoo import fields, models, _
from odoo.exceptions import AccessError


class SaleValidationLog(models.Model):
    _name = 'dumurosol.sale.validation.log'
    _description = "Journal des décisions de validation des devis (traçabilité figée)"
    _order = 'decision_date desc, id desc'
    _log_access = True

    sale_order_id = fields.Many2one(
        'sale.order', string="Devis", required=True,
        ondelete='cascade', index=True)
    rule_id = fields.Many2one(
        'dumurosol.sale.validation.rule', string="Règle appliquée",
        ondelete='restrict', required=True)
    level_id = fields.Many2one(
        'dumurosol.sale.validation.level', string="Niveau concerné",
        ondelete='restrict', required=True)
    level_sequence = fields.Integer(
        string="Ordre du niveau", related='level_id.sequence', store=True)
    validator_user_id = fields.Many2one(
        'res.users', string="Utilisateur ayant statué", required=True)
    decision = fields.Selection([
        ('approved', 'Validé'),
        ('rejected', 'Rejeté'),
    ], string="Décision", required=True)
    comment = fields.Text(string="Commentaire")
    decision_date = fields.Datetime(
        string="Horodatage de la décision", required=True,
        default=fields.Datetime.now)
    company_id = fields.Many2one(
        related='sale_order_id.company_id', store=True, string="Société")

    # ------------------------------------------------------------------
    # Traçabilité figée : aucune modification ni suppression a posteriori,
    # y compris par un profil administrateur technique.
    # ------------------------------------------------------------------
    def write(self, vals):
        raise AccessError(_(
            "Le journal des décisions de validation est en lecture seule : "
            "aucune modification n'est autorisée après enregistrement, "
            "conformément aux exigences de traçabilité et d'audit."))

    def unlink(self):
        raise AccessError(_(
            "Le journal des décisions de validation est en lecture seule : "
            "aucune suppression n'est autorisée, conformément aux exigences "
            "de traçabilité et d'audit."))
