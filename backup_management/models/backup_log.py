# -*- coding: utf-8 -*-
from odoo import fields, models


class BackupLog(models.Model):
    _name = "backup.log"
    _description = "Journal de sauvegarde Google Drive"
    _order = "create_date desc"

    config_id = fields.Many2one(
        "backup.config",
        string="Configuration",
        ondelete="cascade",
        index=True,
    )
    status = fields.Selection(
        [("success", "Succes"), ("failed", "Echec")],
        string="Statut",
        required=True,
    )
    message = fields.Text(string="Message")
    file_size = fields.Integer(string="Taille (octets)")
