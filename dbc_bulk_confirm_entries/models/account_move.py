# -*- coding: utf-8 -*-
import logging

from markupsafe import Markup

from odoo import _, models
from odoo.exceptions import AccessError, RedirectWarning, UserError

_logger = logging.getLogger(__name__)

# Number of failing entries detailed in the user notification.
MAX_REPORTED_FAILURES = 5


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_dbc_bulk_confirm_entries(self):
        """Post every draft journal entry of the recordset in one run.

        Each entry is posted inside its own savepoint, so an entry that cannot
        be posted (unbalanced, locked period, missing data, ...) is reported
        instead of aborting the whole batch.

        :return: an ``ir.actions.client`` notification summarising the run.
        """
        if not self:
            raise UserError(_("Please select at least one journal entry."))

        if not self.env.su and not self.env.user.has_group('account.group_account_invoice'):
            raise AccessError(_("You are not allowed to post journal entries."))

        drafts = self.filtered(lambda move: move.state == 'draft')
        ignored = self - drafts

        posted = self.browse()
        failures = []
        for move in drafts:
            try:
                with self.env.cr.savepoint():
                    move._post(soft=False)
            except (UserError, RedirectWarning) as error:
                failures.append((move, self._dbc_error_message(error)))
            except Exception as error:  # noqa: BLE001 - keep the batch running
                _logger.exception(
                    "Bulk confirm: unexpected error while posting account.move %s", move.id)
                failures.append((move, str(error) or error.__class__.__name__))
            else:
                posted |= move

        self._dbc_log_failures(failures)
        return self._dbc_bulk_confirm_notification(posted, ignored, failures)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _dbc_error_message(error):
        """Return the human readable part of an Odoo exception."""
        if error.args and isinstance(error.args[0], str):
            return error.args[0]
        return str(error) or error.__class__.__name__

    def _dbc_log_failures(self, failures):
        """Trace the reason of each failure in the chatter of the entry."""
        for move, reason in failures:
            move.message_post(body=Markup("<p>%s<br/>%s</p>") % (
                _("Bulk confirmation: this entry could not be posted."),
                reason,
            ))

    def _dbc_bulk_confirm_notification(self, posted, ignored, failures):
        """Build the notification summarising a bulk confirmation run."""
        details = []
        if posted:
            details.append(_("%s entry(ies) confirmed.", len(posted)))
        if ignored:
            details.append(_("%s entry(ies) skipped (not in draft state).", len(ignored)))
        if failures:
            details.append(_("%s entry(ies) could not be confirmed:", len(failures)))
            for move, reason in failures[:MAX_REPORTED_FAILURES]:
                details.append(_("- %(entry)s: %(reason)s",
                                 entry=move.display_name, reason=reason))
            if len(failures) > MAX_REPORTED_FAILURES:
                details.append(_("- ... and %s more (see the entry's chatter).",
                                 len(failures) - MAX_REPORTED_FAILURES))

        if failures:
            notification_type = 'warning' if posted else 'danger'
            title = _("Bulk confirmation partially done") if posted else _("Bulk confirmation failed")
        elif posted:
            notification_type = 'success'
            title = _("Entries confirmed")
        else:
            notification_type = 'warning'
            title = _("Nothing to confirm")
            details = [_("None of the selected entries is in draft state.")]

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': title,
                'message': " ".join(details),
                'type': notification_type,
                'sticky': bool(failures),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }
