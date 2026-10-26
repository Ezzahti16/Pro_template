# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    ma_fiscal_reports_enabled = fields.Boolean(
        string="Activer les formules modifiables",
        config_parameter='ma_formules.reports_enabled',
        help="Les rapports fiscaux marocains sont toujours visibles dans le menu "
             "Comptabilité et fonctionnent par défaut avec les formules communes. "
             "Activer ce réglage permet en plus de personnaliser ces formules pour "
             "cette société (URL/secret de l'instance des formules, enregistrement "
             "du client, et le crayon d'édition des formules dans les rapports).",
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

        Saves the current screen's values first (same as clicking the page's
        own "Save" button), so filling in the URL/secret and clicking this
        button directly works without a separate Save-then-scroll-back step
        — the API URL and secret this is about to use are always the ones
        actually showing on screen, not whatever was saved last. The view's
        `noSaveDialog` attribute on this button is what makes that single
        click actually reach this method in the first place — without it,
        Odoo's settings form shows its own "save changes?" dialog on a
        dirty form and, confusingly, does NOT run this method afterwards
        even if you click its "Save" (see web's SettingsFormController._confirmSave).
        """
        self.ensure_one()
        self.set_values()
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

    def action_ma_unregister_client(self):
        """Delete this client and every formula row it owns on the formules
        instance (every fiscal year — never the master/template rows),
        then clear the locally stored client_id so "Enregistrer ce client"
        can be used again from a clean state. Guarded in the view
        (invisible unless a client_id is set) and here.

        Saves the current screen's values first, same as
        action_ma_register_client and for the same reason (so an edited
        but unsaved URL/secret is the one actually used here, and the
        settings form's own dirty-check dialog never short-circuits this
        button — see the `noSaveDialog` attribute on it in the view).
        """
        self.ensure_one()
        self.set_values()
        icp = self.env['ir.config_parameter'].sudo()
        client_id = icp.get_param('ma_formules.client_id')
        if not client_id:
            raise UserError(_("Aucun client enregistré auprès de l'instance des formules."))

        self.env['account.report']._unregister_client_with_formules_api(client_id)
        icp.set_param('ma_formules.client_id', False)
        self.ma_fiscal_client_id = False

        return {'type': 'ir.actions.client', 'tag': 'reload'}
