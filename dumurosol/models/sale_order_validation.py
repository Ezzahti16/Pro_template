# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from datetime import timedelta


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    current_validation_rule_id = fields.Many2one(
        'dumurosol.sale.validation.rule', string="Règle de validation appliquée",
        copy=False, tracking=True, readonly=True)
    current_validation_level_id = fields.Many2one(
        'dumurosol.sale.validation.level', string="Niveau de validation courant",
        copy=False, tracking=True, readonly=True)
    validation_state = fields.Selection([
        ('none', "Non soumis"),
        ('to_submit', "À soumettre"),
        ('pending', "En attente de validation"),
        ('validated', "Validé"),
        ('rejected', "Rejeté"),
        ('to_correct', "À corriger"),
    ], string="État de validation", default='none', copy=False, tracking=True, index=True)
    validation_log_ids = fields.One2many(
        'dumurosol.sale.validation.log', 'sale_order_id', string="Historique de validation")
    validation_level_deadline = fields.Datetime(
        string="Échéance SLA niveau courant", copy=False, readonly=True)
    is_current_user_validator = fields.Boolean(
        string="Je suis validateur du niveau courant",
        compute='_compute_is_current_user_validator')
    validation_rejection_reason = fields.Text(
        string="Motif de rejet", copy=False, readonly=True)
    validation_submitted_date = fields.Datetime(
        string="Date de soumission au circuit", copy=False, readonly=True,
        help="Date à laquelle ce devis a été soumis pour la première fois au "
             "circuit de validation en cours.")
    validation_step_ids = fields.One2many(
        'dumurosol.sale.order.validation.step', 'sale_order_id',
        string="Circuit de validation et validateurs désignés")

    def _refresh_validation_steps(self):
        StepModel = self.env['dumurosol.sale.order.validation.step']
        for order in self:
            order.validation_step_ids.unlink()

            rule = order.current_validation_rule_id
            if not rule:
                continue

            levels = rule.level_ids.sorted('sequence')
            for level in levels:
                validators = level._get_current_validators()
                validator_names = ", ".join(validators.mapped('name')) or _("aucun validateur configuré")

                level_logs = order.validation_log_ids.filtered(lambda l: l.level_id == level)
                if level_logs:
                    last_log = level_logs.sorted('decision_date')[-1]
                    if last_log.decision == 'approved':
                        status = 'approved'
                        status_label = _("Validé le %s par %s") % (
                            fields.Datetime.to_string(last_log.decision_date),
                            last_log.validator_user_id.name)
                    else:
                        status = 'rejected'
                        status_label = _("Rejeté le %s par %s") % (
                            fields.Datetime.to_string(last_log.decision_date),
                            last_log.validator_user_id.name)
                    if last_log.comment:
                        status_label += _(" — Remarque : %s") % last_log.comment
                elif order.current_validation_level_id.id == level.id \
                        and order.validation_state == 'pending':
                    status = 'pending'
                    status_label = _("En attente de décision")
                else:
                    status = 'not_reached'
                    status_label = _("Pas encore atteint")

                StepModel.create({
                    'sale_order_id': order.id,
                    'level_id': level.id,
                    'level_sequence': level.sequence,
                    'level_name': level.name,
                    'validator_names': validator_names,
                    'status': status,
                    'status_label': status_label,
                })

    @api.depends('current_validation_level_id', 'validation_state')
    def _compute_is_current_user_validator(self):
        user = self.env.user
        for order in self:
            is_validator = False
            if order.validation_state == 'pending' and order.current_validation_level_id:
                validators = order.current_validation_level_id._get_current_validators()
                if user in validators:
                    if not order.current_validation_rule_id.allow_self_validation \
                            and order.create_uid == user:
                        is_validator = False
                    else:
                        is_validator = True
            order.is_current_user_validator = is_validator

    # ------------------------------------------------------------------
    # Surcharge de la confirmation : calcul de la règle applicable et
    # initialisation du circuit avant le passage en commande confirmée.
    # ------------------------------------------------------------------
    def action_confirm(self):
        self._check_no_divers_product_line()

        already_pending = self.filtered(lambda o: o.validation_state == 'pending')
        if already_pending:
            details = "\n".join([
                _("- %(name)s : soumis le %(date)s, en attente au niveau « %(level)s ».") % {
                    'name': o.name,
                    'date': fields.Datetime.to_string(o.validation_submitted_date) or _("date inconnue"),
                    'level': o.current_validation_level_id.name or _("indéterminé"),
                }
                for o in already_pending
            ])
            raise UserError(_(
                "Une demande d'approbation a déjà été effectuée pour ce devis "
                "et est toujours en cours de traitement :\n%s\n\n"
                "Merci d'attendre la décision du ou des validateurs concernés "
                "plutôt que de soumettre une nouvelle demande."
            ) % details)

        already_settled = self.filtered(lambda o: o.state in ('sale', 'cancel'))
        to_process = self - already_settled

        for order in to_process:
            if order.validation_state == 'validated':
                continue
            order._start_validation_workflow()

        orders_to_hold = to_process.filtered(
            lambda o: o.validation_state == 'pending')
        orders_ready = to_process - orders_to_hold
        res = True
        if orders_ready:
            res = super(SaleOrder, orders_ready).action_confirm()
        return res

    def _start_validation_workflow(self):
        self.ensure_one()
        RuleModel = self.env['dumurosol.sale.validation.rule']
        amount = self.amount_untaxed
        partner = self.partner_id
        products = self.order_line.filtered(lambda l: not l.display_type).mapped('product_id')
        company = self.company_id
        max_discount = max(
            self.order_line.filtered(lambda l: not l.display_type).mapped('discount') or [0.0])

        rule = RuleModel._find_applicable_rule(amount, partner, products, company, max_discount)

        if not rule:
            raise UserError(_(
                "Aucune règle de validation applicable n'a été trouvée pour ce "
                "devis, et aucune règle par défaut (fallback) n'est configurée. "
                "Merci de contacter l'administrateur du module Workflow Ventes "
                "pour configurer une règle par défaut."
            ))

        levels = rule.level_ids.sorted('sequence')
        self.write({
            'current_validation_rule_id': rule.id,
            'current_validation_level_id': levels[0].id if levels else False,
            'validation_state': 'pending' if levels else 'validated',
            'validation_submitted_date': fields.Datetime.now(),
        })
        if levels:
            self._set_level_deadline(levels[0])
            self._notify_level_validators(levels[0], first=True)
        self.message_post(body=_(
            "Circuit de validation initialisé avec la règle « %s » (%s niveau(x))."
        ) % (rule.name, len(levels)))
        self._refresh_validation_steps()

    def _set_level_deadline(self, level):
        self.ensure_one()
        if level.sla_hours:
            deadline = fields.Datetime.now() + timedelta(hours=level.sla_hours)
        else:
            deadline = False
        self.validation_level_deadline = deadline

    def _notify_level_validators(self, level, first=False):
        self.ensure_one()
        validators = level._get_current_validators()
        if not validators:
            self._escalate_level(level, no_titular=True)
            return

        self.message_post(
            body=_(
                "Validation requise au niveau « %(level)s » pour le devis "
                "%(name)s (%(partner)s, %(amount).2f %(currency)s). "
                "Règle appliquée : %(rule)s."
            ) % {
                'level': level.name,
                'name': self.name,
                'partner': self.partner_id.name,
                'amount': self.amount_untaxed,
                'currency': self.currency_id.name,
                'rule': self.current_validation_rule_id.name,
            },
            partner_ids=validators.mapped('partner_id').ids,
            subtype_xmlid='mail.mt_comment',
        )

        for user in validators:
            self.activity_schedule(
                'mail.mail_activity_data_todo',
                summary=_("Validation requise : %s") % level.name,
                note=_(
                    "Le devis %(name)s (%(partner)s, %(amount).2f %(currency)s) "
                    "requiert votre validation au niveau « %(level)s »."
                ) % {
                    'name': self.name,
                    'partner': self.partner_id.name,
                    'amount': self.amount_untaxed,
                    'currency': self.currency_id.name,
                    'level': level.name,
                },
                user_id=user.id,
            )

    # ------------------------------------------------------------------
    # Actions de validation / rejet (boutons contextuels vue formulaire)
    # ------------------------------------------------------------------
    def action_validate_current_level(self, remark=None):
        self.ensure_one()
        self._check_can_decide()
        level = self.current_validation_level_id
        rule = self.current_validation_rule_id

        self.env['dumurosol.sale.validation.log'].create({
            'sale_order_id': self.id,
            'rule_id': rule.id,
            'level_id': level.id,
            'validator_user_id': self.env.user.id,
            'decision': 'approved',
            'comment': remark or False,
            'decision_date': fields.Datetime.now(),
        })
        self._close_pending_activities(level)

        next_level = rule.level_ids.filtered(
            lambda l: l.sequence > level.sequence).sorted('sequence')[:1]

        if next_level:
            self.write({
                'current_validation_level_id': next_level.id,
            })
            self._set_level_deadline(next_level)
            self._notify_level_validators(next_level)
            self.message_post(body=_(
                "Niveau « %s » validé par %s. Passage au niveau « %s »."
            ) % (level.name, self.env.user.name, next_level.name))
        else:
            self.write({
                'validation_state': 'validated',
                'current_validation_level_id': False,
                'validation_level_deadline': False,
            })
            self.message_post(body=_(
                "Dernier niveau « %s » validé par %s. Devis entièrement validé."
            ) % (level.name, self.env.user.name))
            self._notify_requester_validated()
            # Poursuite de la confirmation standard Odoo (état "sale",
            # verrouillage automatique, etc.)
            super(SaleOrder, self).action_confirm()

        self._refresh_validation_steps()

    def action_reject_current_level(self, reason=None):
        self.ensure_one()
        self._check_can_decide()
        if not reason:
            raise UserError(_("Un motif de rejet est obligatoire."))
        level = self.current_validation_level_id
        rule = self.current_validation_rule_id

        self.env['dumurosol.sale.validation.log'].create({
            'sale_order_id': self.id,
            'rule_id': rule.id,
            'level_id': level.id,
            'validator_user_id': self.env.user.id,
            'decision': 'rejected',
            'comment': reason,
            'decision_date': fields.Datetime.now(),
        })
        self._close_pending_activities(level)

        new_state = rule.rejection_state or 'to_correct'
        self.write({
            'validation_state': new_state,
            'validation_rejection_reason': reason,
            'current_validation_level_id': False,
            'validation_level_deadline': False,
        })
        self.message_post(body=_(
            "Niveau « %s » rejeté par %s. Motif : %s"
        ) % (level.name, self.env.user.name, reason))
        self._notify_requester_rejected(reason)
        self._refresh_validation_steps()

    def _check_can_decide(self):
        self.ensure_one()
        if self.validation_state != 'pending' or not self.current_validation_level_id:
            raise UserError(_("Ce devis n'est pas en attente de validation."))
        level = self.current_validation_level_id
        validators = level._get_current_validators()
        if self.env.user not in validators:
            raise UserError(_(
                "Vous n'êtes pas habilité à valider ce niveau du circuit."))
        if not self.current_validation_rule_id.allow_self_validation \
                and self.create_uid == self.env.user:
            raise UserError(_(
                "Séparation des tâches : vous ne pouvez pas valider un devis "
                "dont vous êtes l'auteur."))

    def _close_pending_activities(self, level):
        self.ensure_one()
        activities = self.activity_ids.filtered(
            lambda a: a.summary and level.name in (a.summary or ''))
        activities.unlink()

    # ------------------------------------------------------------------
    # Re-soumission après correction
    # ------------------------------------------------------------------
    def action_resubmit_validation(self):
        for order in self:
            if order.validation_state == 'pending':
                raise UserError(_(
                    "Une demande d'approbation a déjà été effectuée pour « %(name)s » "
                    "le %(date)s et est toujours en cours de traitement (niveau "
                    "« %(level)s »). Merci d'attendre la décision du validateur."
                ) % {
                    'name': order.name,
                    'date': fields.Datetime.to_string(order.validation_submitted_date) or _("date inconnue"),
                    'level': order.current_validation_level_id.name or _("indéterminé"),
                })
            if order.validation_state != 'to_correct':
                raise UserError(_(
                    "Seuls les devis à l'état « À corriger » peuvent être "
                    "re-soumis."))
            order._resubmit_after_correction()

    def _resubmit_after_correction(self):
        self.ensure_one()
        rule = self.current_validation_rule_id
        products = self.order_line.filtered(lambda l: not l.display_type).mapped('product_id')
        max_discount = max(
            self.order_line.filtered(lambda l: not l.display_type).mapped('discount') or [0.0])
        new_rule = self.env['dumurosol.sale.validation.rule']._find_applicable_rule(
            self.amount_untaxed, self.partner_id, products, self.company_id, max_discount)
        if not new_rule:
            raise UserError(_(
                "Aucune règle applicable après correction, et aucune règle par "
                "défaut configurée."))

        rule_changed = new_rule.id != rule.id
        levels = new_rule.level_ids.sorted('sequence')

        if rule_changed or new_rule.resubmit_from_level_1:
            start_level = levels[0] if levels else False
            self.message_post(body=_(
                "Re-soumission : circuit recalculé depuis le niveau 1 (règle "
                "applicable : « %s »). Historique du circuit précédent conservé."
            ) % new_rule.name)
        else:
            rejected_level = self.validation_log_ids.filtered(
                lambda l: l.decision == 'rejected')[:1].level_id
            remaining = levels.filtered(lambda l: l.sequence >= rejected_level.sequence)
            start_level = remaining[0] if remaining else levels[0]
            self.message_post(body=_(
                "Re-soumission : reprise du circuit au niveau « %s » (paramétrage "
                "de la règle)."
            ) % start_level.name)

        self.write({
            'current_validation_rule_id': new_rule.id,
            'current_validation_level_id': start_level.id if start_level else False,
            'validation_state': 'pending' if start_level else 'validated',
            'validation_rejection_reason': False,
            'validation_submitted_date': fields.Datetime.now() if start_level else False,
        })
        if start_level:
            self._set_level_deadline(start_level)
            self._notify_level_validators(start_level)
        else:
            super(SaleOrder, self).action_confirm()

        self._refresh_validation_steps()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _notify_requester_validated(self):
        self.ensure_one()
        if not self.create_uid:
            return
        self.message_post(
            body=_(
                "Votre devis %s a été validé à tous les niveaux du circuit et "
                "peut désormais être confirmé en commande."
            ) % self.name,
            partner_ids=[self.create_uid.partner_id.id],
            subtype_xmlid='mail.mt_comment',
        )

    def _notify_requester_rejected(self, reason):
        self.ensure_one()
        if not self.create_uid:
            return
        self.message_post(
            body=_(
                "Votre devis %(name)s a été rejeté.\nMotif : %(reason)s\n"
                "Merci d'apporter les corrections nécessaires puis de re-soumettre "
                "le devis."
            ) % {'name': self.name, 'reason': reason},
            partner_ids=[self.create_uid.partner_id.id],
            subtype_xmlid='mail.mt_comment',
        )

    def _escalate_level(self, level, no_titular=False, sla_breach=False):
        self.ensure_one()
        if not level.escalation_user_id:
            self.message_post(body=_(
                "Alerte : le niveau « %s » n'a pas de destinataire d'escalade "
                "configuré. Intervention manuelle requise."
            ) % level.name)
            return
        reason = _("aucun validateur configuré pour ce niveau") if no_titular \
            else _("dépassement du délai SLA")
        self.activity_schedule(
            'mail.mail_activity_data_todo',
            summary=_("Escalade validation : %s") % level.name,
            note=_(
                "Escalade pour le devis %(name)s au niveau « %(level)s » (%(reason)s)."
            ) % {'name': self.name, 'level': level.name, 'reason': reason},
            user_id=level.escalation_user_id.id,
        )
        self.message_post(body=_(
            "Escalade du niveau « %s » vers %s (%s)."
        ) % (level.name, level.escalation_user_id.name, reason))

    # ------------------------------------------------------------------
    # Cron : détection des dépassements de SLA
    # ------------------------------------------------------------------
    @api.model
    def _cron_check_validation_sla(self):
        overdue = self.search([
            ('validation_state', '=', 'pending'),
            ('validation_level_deadline', '!=', False),
            ('validation_level_deadline', '<=', fields.Datetime.now()),
        ])
        for order in overdue:
            level = order.current_validation_level_id
            if not level:
                continue
            validators = level._get_current_validators()
            for user in validators:
                order.activity_schedule(
                    'mail.mail_activity_data_todo',
                    summary=_("Relance : validation en attente"),
                    note=_(
                        "Le délai de traitement du niveau « %s » est dépassé pour "
                        "ce devis. Merci de le traiter rapidement."
                    ) % level.name,
                    user_id=user.id,
                )
            if validators:
                order.message_post(
                    body=_(
                        "Relance : le délai de traitement du niveau « %s » est "
                        "dépassé pour ce devis. Merci de le traiter rapidement."
                    ) % level.name,
                    partner_ids=validators.mapped('partner_id').ids,
                    subtype_xmlid='mail.mt_comment',
                )
            if level.escalation_hours:
                escalation_deadline = order.validation_level_deadline + timedelta(
                    hours=(level.escalation_hours - level.sla_hours))
                if fields.Datetime.now() >= escalation_deadline:
                    order._escalate_level(level, sla_breach=True)
            order.validation_level_deadline = fields.Datetime.now() + timedelta(
                hours=level.sla_hours or 24)
