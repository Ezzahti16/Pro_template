# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class SaleValidationRule(models.Model):
    _name = 'dumurosol.sale.validation.rule'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Règle de validation des devis"
    _order = 'sequence, id'
    _rec_name = 'name'

    name = fields.Char(string="Nom de la règle", required=True, tracking=True)
    active = fields.Boolean(string="Active", default=True, tracking=True)
    state = fields.Selection([
        ('draft', 'Brouillon'),
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ], string="Statut", default='draft', required=True, tracking=True, copy=False)
    company_id = fields.Many2one(
        'res.company', string="Société", required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', string="Devise", store=True, readonly=True)
    sequence = fields.Integer(string="Priorité / Ordre d'affichage", default=10)
    is_default_rule = fields.Boolean(
        string="Règle par défaut (fallback)", tracking=True,
        help="Règle sans critère discriminant, appliquée quand aucune autre règle ne "
             "correspond au devis.")

    # --- Critère a) Montant ---
    use_amount_criteria = fields.Boolean(string="Activer le critère Montant")
    amount_min = fields.Monetary(string="Montant minimum (inclus)", currency_field='currency_id')
    amount_max = fields.Monetary(
        string="Montant maximum (inclus)", currency_field='currency_id',
        help="Laisser vide pour signifier 'infini' (aucune borne haute).")

    # --- Critère b) Client ---
    use_customer_criteria = fields.Boolean(string="Activer le critère Client")
    partner_ids = fields.Many2many(
        'res.partner', 'dumurosol_sale_validation_rule_partner_rel',
        'rule_id', 'partner_id', string="Clients concernés",
        domain=[('customer_rank', '>', 0)])
    partner_category_ids = fields.Many2many(
        'res.partner.category', 'dumurosol_sale_validation_rule_partner_category_rel',
        'rule_id', 'category_id', string="Tags clients concernés")

    # --- Critère c) Produit / catégorie ---
    use_product_criteria = fields.Boolean(string="Activer le critère Produit")
    product_ids = fields.Many2many(
        'product.product', 'dumurosol_sale_validation_rule_product_rel',
        'rule_id', 'product_id', string="Produits concernés",
        domain=[('sale_ok', '=', True)])
    product_category_ids = fields.Many2many(
        'product.category', 'dumurosol_sale_validation_rule_category_rel',
        'rule_id', 'category_id', string="Catégories de produits concernées")
    include_child_categories = fields.Boolean(
        string="Inclure les sous-catégories", default=True)

    # --- Critère d) Remise ---
    use_discount_criteria = fields.Boolean(string="Activer le critère Remise")
    min_discount_percent = fields.Float(
        string="Remise minimale déclenchante (%)",
        help="La règle s'applique si au moins une ligne du devis a une remise "
             "supérieure ou égale à ce pourcentage.")

    # --- Circuit de validation ---
    level_ids = fields.One2many(
        'dumurosol.sale.validation.level', 'rule_id', string="Niveaux du circuit", copy=True)
    level_count = fields.Integer(compute='_compute_level_count', string="Nb de niveaux")

    # --- Gestion des cas limites ---
    rejection_state = fields.Selection([
        ('to_correct', 'À corriger'),
        ('rejected', 'Rejetée'),
    ], string="État après rejet", default='to_correct', required=True,
        help="État du devis suite à un rejet à n'importe quel niveau.")
    resubmit_from_level_1 = fields.Boolean(
        string="Reprise au niveau 1 après correction", default=True,
        help="Si décoché, le devis reprend au niveau où il a été rejeté lors "
             "d'une nouvelle soumission.")
    allow_self_validation = fields.Boolean(
        string="Autoriser l'auto-validation", default=False,
        help="Si activé, un utilisateur peut valider un devis dont il est "
             "l'auteur, même s'il est désigné validateur (déroge à la séparation des "
             "tâches). Paramètre à activer avec prudence.")

    # --- Diagnostic / conflits ---
    conflict_rule_ids = fields.Many2many(
        'dumurosol.sale.validation.rule', 'dumurosol_sale_validation_rule_conflict_rel',
        'rule_id', 'conflict_id', string="Règles en conflit")
    has_conflict = fields.Boolean(string="En conflit", default=False)
    usage_count = fields.Integer(
        string="Devis traités (période)", compute='_compute_usage_count')

    _sql_constraints = [
        ('amount_range_check',
         'CHECK (amount_max IS NULL OR amount_min IS NULL OR amount_max >= amount_min)',
         "Le montant maximum doit être supérieur ou égal au montant minimum."),
    ]

    @api.depends('level_ids')
    def _compute_level_count(self):
        for rule in self:
            rule.level_count = len(rule.level_ids)

    def _compute_usage_count(self):
        for rule in self:
            rule.usage_count = self.env['dumurosol.sale.validation.log'].search_count(
                [('rule_id', '=', rule.id)])

    # ------------------------------------------------------------------
    # Contraintes de cohérence
    # ------------------------------------------------------------------
    @api.constrains('use_amount_criteria', 'use_customer_criteria', 'use_product_criteria',
                     'use_discount_criteria', 'is_default_rule')
    def _check_at_least_one_criteria_or_default(self):
        for rule in self:
            if rule.is_default_rule:
                continue
            if not (rule.use_amount_criteria or rule.use_customer_criteria
                    or rule.use_product_criteria or rule.use_discount_criteria):
                raise ValidationError(_(
                    "La règle « %s » doit activer au moins un critère de déclenchement, "
                    "ou être marquée comme règle par défaut (fallback)."
                ) % rule.name)

    @api.constrains('is_default_rule', 'company_id', 'active')
    def _check_single_default_rule_per_company(self):
        for rule in self:
            if rule.is_default_rule and rule.active:
                others = self.search([
                    ('id', '!=', rule.id),
                    ('company_id', '=', rule.company_id.id),
                    ('is_default_rule', '=', True),
                    ('active', '=', True),
                ])
                if others:
                    raise ValidationError(_(
                        "Une seule règle par défaut active est autorisée par société. "
                        "La règle « %s » est déjà définie comme règle par défaut pour %s."
                    ) % (others[0].name, rule.company_id.name))

    @api.constrains('level_ids', 'state')
    def _check_has_levels_when_active(self):
        for rule in self:
            if rule.state == 'active' and not rule.level_ids:
                raise ValidationError(_(
                    "La règle « %s » ne peut être activée sans au moins un niveau de "
                    "circuit de validation défini."
                ) % rule.name)

    # ------------------------------------------------------------------
    # Mécanisme anti-redondance
    # ------------------------------------------------------------------
    def _get_amount_range(self):
        self.ensure_one()
        if not self.use_amount_criteria:
            return (None, None)
        return (self.amount_min or 0.0, self.amount_max or None)

    def _ranges_overlap(self, r1, r2):
        min1, max1 = r1
        min2, max2 = r2
        if min1 is None or min2 is None:
            return True
        max1 = max1 if max1 is not None else float('inf')
        max2 = max2 if max2 is not None else float('inf')
        return min1 <= max2 and min2 <= max1

    def _get_expanded_category_ids(self):
        self.ensure_one()
        if not self.use_product_criteria or not self.product_category_ids:
            return self.env['product.category']
        categories = self.product_category_ids
        if self.include_child_categories:
            all_children = self.env['product.category']
            for cat in categories:
                all_children |= self.env['product.category'].search(
                    [('id', 'child_of', cat.id)])
            categories = categories | all_children
        return categories

    def _get_partner_scope(self):
        self.ensure_one()
        if not self.use_customer_criteria:
            return None
        return (set(self.partner_ids.ids), set(self.partner_category_ids.ids))

    def _get_product_scope(self):
        self.ensure_one()
        if not self.use_product_criteria:
            return None
        categories = self._get_expanded_category_ids()
        return (set(self.product_ids.ids), set(categories.ids))

    def _partner_scopes_overlap(self, scope1, scope2):
        if scope1 is None or scope2 is None:
            return True
        partners1, cats1 = scope1
        partners2, cats2 = scope2
        if not partners1 and not cats1:
            return True
        if not partners2 and not cats2:
            return True
        if partners1 & partners2:
            return True
        if cats1 & cats2:
            return True
        if partners1 and cats2:
            partners1_recs = self.env['res.partner'].browse(list(partners1))
            if partners1_recs.category_id.ids and set(partners1_recs.category_id.ids) & cats2:
                return True
        if partners2 and cats1:
            partners2_recs = self.env['res.partner'].browse(list(partners2))
            if partners2_recs.category_id.ids and set(partners2_recs.category_id.ids) & cats1:
                return True
        return False

    def _product_scopes_overlap(self, scope1, scope2):
        if scope1 is None or scope2 is None:
            return True
        products1, cats1 = scope1
        products2, cats2 = scope2
        if not products1 and not cats1:
            return True
        if not products2 and not cats2:
            return True
        if products1 & products2:
            return True
        if cats1 & cats2:
            return True
        if products1 and cats2:
            products1_recs = self.env['product.product'].browse(list(products1))
            if set(products1_recs.categ_id.ids) & cats2:
                return True
        if products2 and cats1:
            products2_recs = self.env['product.product'].browse(list(products2))
            if set(products2_recs.categ_id.ids) & cats1:
                return True
        return False

    def _overlaps_with(self, other_rule):
        self.ensure_one()
        if self.is_default_rule or other_rule.is_default_rule:
            return False

        amount_overlap = self._ranges_overlap(
            self._get_amount_range(), other_rule._get_amount_range())
        partner_overlap = self._partner_scopes_overlap(
            self._get_partner_scope(), other_rule._get_partner_scope())
        product_overlap = self._product_scopes_overlap(
            self._get_product_scope(), other_rule._get_product_scope())
        # Le critère remise n'est volontairement pas discriminant pour la
        # détection de conflit : deux règles avec des seuils de remise
        # différents mais des périmètres montant/client/produit identiques
        # resteraient ambiguës (un devis peut dépasser plusieurs seuils à la
        # fois), donc on les considère bien en conflit potentiel.

        return amount_overlap and partner_overlap and product_overlap

    def _recompute_conflicts(self):
        companies = self.mapped('company_id')
        if not companies:
            return
        all_rules = self.search([
            ('company_id', 'in', companies.ids),
            ('active', '=', True),
            ('is_default_rule', '=', False),
        ])
        for rule in all_rules:
            candidates = all_rules - rule
            conflicts = candidates.filtered(lambda r: rule._overlaps_with(r))
            rule.with_context(skip_conflict_recompute=True).write({
                'conflict_rule_ids': [(6, 0, conflicts.ids)],
                'has_conflict': bool(conflicts),
            })
        others = self - all_rules
        if others:
            others.with_context(skip_conflict_recompute=True).write(
                {'conflict_rule_ids': [(5, 0, 0)], 'has_conflict': False})

    def _check_conflicts_and_raise(self):
        for rule in self:
            if rule.is_default_rule or not rule.active:
                continue
            candidates = self.search([
                ('id', '!=', rule.id),
                ('company_id', '=', rule.company_id.id),
                ('active', '=', True),
                ('is_default_rule', '=', False),
            ])
            conflicts = candidates.filtered(lambda r: rule._overlaps_with(r))
            if conflicts:
                names = ", ".join(conflicts.mapped('name'))
                raise ValidationError(_(
                    "Impossible d'activer/enregistrer la règle « %s » : son périmètre "
                    "chevauche celui de la ou des règles suivantes : %s.\n\n"
                    "Merci de restreindre les critères (montant, client, produit/"
                    "catégorie) afin de lever toute ambiguïté sur la règle applicable."
                ) % (rule.name, names))

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules.filtered(lambda r: r.active)._check_conflicts_and_raise()
        rules._recompute_conflicts()
        return rules

    def write(self, vals):
        if self.env.context.get('skip_conflict_recompute'):
            return super().write(vals)
        res = super().write(vals)
        relevant_fields = {
            'active', 'use_amount_criteria', 'amount_min', 'amount_max',
            'use_customer_criteria', 'partner_ids', 'partner_category_ids',
            'use_product_criteria', 'product_ids', 'product_category_ids',
            'include_child_categories', 'is_default_rule', 'state', 'company_id',
        }
        if relevant_fields & set(vals.keys()):
            self.filtered(lambda r: r.active)._check_conflicts_and_raise()
            self._recompute_conflicts()
        return res

    def action_activate(self):
        for rule in self:
            rule.active = True
            rule.state = 'active'
        self._check_conflicts_and_raise()

    def action_deactivate(self):
        self.write({'state': 'inactive', 'active': False})

    def action_set_draft(self):
        self.write({'state': 'draft'})

    def action_duplicate_rule(self):
        self.ensure_one()
        new_rule = self.copy({
            'name': _("%s (copie)") % self.name,
            'state': 'draft',
            'active': False,
        })
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'dumurosol.sale.validation.rule',
            'view_mode': 'form',
            'res_id': new_rule.id,
        }

    def action_test_rule(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Tester cette configuration"),
            'res_model': 'dumurosol.sale.validation.test.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_company_id': self.company_id.id},
        }

    # ------------------------------------------------------------------
    # Moteur de résolution de règle applicable
    # ------------------------------------------------------------------
    def _rule_matches(self, amount, partner, products, company, max_discount=0.0):
        self.ensure_one()
        if self.company_id != company:
            return False
        if self.is_default_rule:
            return True

        if self.use_amount_criteria:
            amt_min = self.amount_min or 0.0
            amt_max = self.amount_max or None
            if amount < amt_min:
                return False
            if amt_max is not None and amount > amt_max:
                return False

        if self.use_customer_criteria:
            partner_match = False
            if self.partner_ids and partner.id in self.partner_ids.ids:
                partner_match = True
            if not partner_match and self.partner_category_ids:
                if set(partner.category_id.ids) & set(self.partner_category_ids.ids):
                    partner_match = True
            if not partner_match:
                return False

        if self.use_product_criteria:
            product_match = False
            product_ids = products.ids if products else []
            categ_ids = list(set(products.mapped('categ_id').ids)) if products else []
            if self.product_ids and set(product_ids) & set(self.product_ids.ids):
                product_match = True
            if not product_match and self.product_category_ids:
                expanded_categs = self._get_expanded_category_ids()
                if set(categ_ids) & set(expanded_categs.ids):
                    product_match = True
            if not product_match:
                return False

        if self.use_discount_criteria:
            if max_discount < (self.min_discount_percent or 0.0):
                return False

        return True

    @api.model
    def _find_applicable_rule(self, amount, partner, products, company, max_discount=0.0):
        # 'active' is only the archive flag: a rule can be un-archived but
        # still in 'draft' (never explicitly activated) or 'inactive'. Only
        # rules explicitly activated (state == 'active') may ever apply.
        specific_rules = self.search([
            ('company_id', '=', company.id),
            ('active', '=', True),
            ('state', '=', 'active'),
            ('is_default_rule', '=', False),
        ], order='sequence, id')
        for rule in specific_rules:
            if rule._rule_matches(amount, partner, products, company, max_discount):
                return rule

        default_rule = self.search([
            ('company_id', '=', company.id),
            ('active', '=', True),
            ('state', '=', 'active'),
            ('is_default_rule', '=', True),
        ], limit=1)
        return default_rule
