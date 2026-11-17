from odoo import fields, models


class TamForceCreateWizard(models.TransientModel):
    """Hands the publisher-notification refusal back to the user as a choice.

    The client database is left exactly as it was when this opens: nothing has
    been created yet, so cancelling here is the old behaviour and confirming
    creates the temporary admin with its full lifetime, knowing the
    notification may count it.
    """

    _name = "tam.force.create.wizard"
    _description = "Create a temporary admin despite the publisher notification"

    database_ids = fields.Many2many("tam.database", string="Databases",
                                    readonly=True)
    message = fields.Text(readonly=True)

    def action_confirm(self):
        self.ensure_one()
        if not self.database_ids:
            return {"type": "ir.actions.act_window_close"}
        res = self.database_ids.with_context(
            tam_force_publisher=True).action_create_temp_user()
        # True means "created, nothing more to show": closing the dialog reloads
        # the view behind it, which is where the result is read.
        return res if isinstance(res, dict) else {"type": "ir.actions.act_window_close"}
