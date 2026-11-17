# -*- coding: utf-8 -*-
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class DriveBackupController(http.Controller):

    @http.route(
        "/drive_backup/oauth/callback",
        type="http",
        auth="user",
        csrf=False,
        save_session=False,
    )
    def oauth_callback(self, **kw):
        error = kw.get("error")
        if error:
            return "Erreur d'autorisation Google : %s" % error

        code = kw.get("code")
        state = kw.get("state")
        if not code or not state:
            return "Parametres manquants (code / state)."

        try:
            config_id = int(state)
        except (TypeError, ValueError):
            return "State invalide."

        config = request.env["backup.config"].browse(config_id)
        if not config.exists():
            return "Configuration inconnue."

        config._exchange_oauth_code(code)
        request.env.cr.commit()

        return (
            "<html><head><meta charset='utf-8'></head><body style='font-family:sans-serif'>"
            "<h3>Autorisation Google Drive reussie.</h3>"
            "<p>Vous pouvez fermer cet onglet et revenir a Odoo.</p>"
            "</body></html>"
        )
