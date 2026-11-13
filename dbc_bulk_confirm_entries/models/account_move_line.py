# -*- coding: utf-8 -*-
from odoo import _, models
from odoo.exceptions import UserError


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def action_dbc_bulk_confirm_entries(self):
        """Confirm the draft journal entries owning the selected journal items.

        Entry point of the server action bound to the journal items list
        (``account.view_move_line_tree``). Several lines may belong to the same
        entry: ``move_id`` deduplicates them, so each entry is posted once and
        the summary counts entries, not lines.

        :return: an ``ir.actions.client`` notification summarising the run.
        """
        if not self:
            raise UserError(_("Please select at least one journal item."))

        moves = self.move_id
        if not moves:
            raise UserError(_("The selected journal items are not linked to any journal entry."))

        return moves.action_dbc_bulk_confirm_entries()
