# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ma_fiscal_reports_enabled = fields.Boolean(
        string="Activer les rapports fiscal",
        config_parameter='ma_formules.reports_enabled',
        help="Affiche les rapports fiscaux marocains dans le menu Comptabilité. "
             "Désactivé, ces rapports restent masqués pour tous les utilisateurs.",
    )
    ma_fiscal_api_url = fields.Char(
        string="URL de l'instance des formules",
        config_parameter='ma_formules.api_url',
        help="Ex: http://app.ozirpaie.ma:2032/api/ma/formules pour tester sur "
             "l'instance de test, http://app.ozirpaie.ma:2024/api/ma/formules "
             "(valeur par défaut) pour l'instance de production.",
    )
    ma_fiscal_register_secret = fields.Char(
        string="Secret d'enregistrement (instance des formules)",
        config_parameter='ma_formules.register_secret',
    )
    ma_fiscal_client_id = fields.Char(
        string="Identifiant client (formules)",
        config_parameter='ma_formules.client_id',
        readonly=True,
    )

    def set_values(self):
        super().set_values()
        self._ma_sync_reports_group()

    def _ma_sync_reports_group(self):
        group = self.env.ref(
            'dbc_liasse_fiscale.group_ma_fiscal_reports_enabled', raise_if_not_found=False,
        )
        if not group:
            return
        if self.ma_fiscal_reports_enabled:
            readonly_group = self.env.ref('account.group_account_readonly', raise_if_not_found=False)
            user_ids = readonly_group.all_user_ids.ids if readonly_group else []
            group.write({'user_ids': [(6, 0, user_ids)]})
        else:
            group.write({'user_ids': [(6, 0, [])]})

    def action_ma_register_client(self):
        """One-time setup action: register this company as a client of the
        formules instance and have it clone the master formulas for every
        fiscal year (2020 -> current). Guarded both in the view
        (invisible unless enabled and not yet registered) and here, so
        calling it twice is a no-op rather than a duplicate registration.
        """
        self.ensure_one()
        icp = self.env['ir.config_parameter'].sudo()
        if icp.get_param('ma_formules.client_id'):
            raise UserError(_("Ce client est déjà enregistré auprès de l'instance des formules."))

        client_id = self.env['account.report']._register_client_with_formules_api(
            self.env.company.name,
        )
        icp.set_param('ma_formules.client_id', client_id)
        self.ma_fiscal_client_id = client_id

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _("Client enregistré"),
                'message': _("Identifiant client : %s. Les formules de chaque exercice "
                             "(2020 à aujourd'hui) ont été initialisées.") % client_id,
                'sticky': False,
                'type': 'success',
            },
        }
