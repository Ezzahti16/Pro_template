# -*- coding: utf-8 -*-
from odoo import fields, models


class SaleOrderValidationStep(models.Model):
    _name = 'dumurosol.sale.order.validation.step'
    _description = "Étape du circuit de validation pour un devis (snapshot matérialisé)"
    _order = 'sale_order_id, level_sequence'

    # Ce modèle n'est pas un champ calculé à la volée : ses enregistrements
    # sont créés/mis à jour explicitement en Python à chaque transition du
    # circuit (soumission, décision, re-soumission), pour éviter les
    # problèmes de cache des champs `compute` dépendant du contexte de vue.
    sale_order_id = fields.Many2one(
        'sale.order', string="Devis", required=True, ondelete='cascade')
    level_id = fields.Many2one(
        'dumurosol.sale.validation.level', string="Niveau", required=True, ondelete='cascade')
    level_sequence = fields.Integer(string="Ordre du niveau")
    level_name = fields.Char(string="Libellé du niveau")
    validator_names = fields.Char(string="Validateur(s)")
    status = fields.Selection([
        ('not_reached', "Pas encore atteint"),
        ('pending', "En attente de décision"),
        ('approved', "Validé"),
        ('rejected', "Rejeté"),
    ], string="Étape", default='not_reached', required=True)
    status_label = fields.Char(string="Étape (détail)")
