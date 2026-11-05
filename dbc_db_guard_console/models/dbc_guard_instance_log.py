# Part of dbc_db_guard_console. See LICENSE file for full copyright details.

from odoo import fields, models

ACTIONS = [
    ('test', "Test connection"),
    ('fetch', "Fetch configuration"),
    ('apply', "Apply configuration"),
    ('rollback', "Rollback"),
    ('cron', "Scheduled check"),
]


class DbcGuardInstanceLog(models.Model):
    _name = 'dbc.guard.instance.log'
    _description = "Managed Instance History"
    _order = 'create_date desc, id desc'

    instance_id = fields.Many2one(
        'dbc.guard.instance', string="Instance", required=True,
        ondelete='cascade', index=True,
    )
    action = fields.Selection(ACTIONS, required=True)
    success = fields.Boolean()
    message = fields.Text()
    user_id = fields.Many2one(
        'res.users', string="Done by", default=lambda self: self.env.user, readonly=True,
    )
