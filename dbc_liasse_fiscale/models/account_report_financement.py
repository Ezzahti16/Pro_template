# -*- coding: utf-8 -*-
from odoo import models


class AccountReportHandlerMaFiscal_Financement(models.AbstractModel):
    """Activates the 2-table screen layout (see
    static/src/js/account_report_financement.xml) for
    bilan_financement_report only. Every other report using this same
    shared custom_handler_model_id (account.report.handler.ma_fiscal) is
    unaffected: it keeps the default, single-table account_reports layout.
    """
    _inherit = 'account.report.handler.ma_fiscal'

    def _custom_options_initializer(self, report, options, previous_options):
        super()._custom_options_initializer(report, options, previous_options)

        if report == report.env.ref('dbc_liasse_fiscale.bilan_financement_report', raise_if_not_found=False):
            options['custom_display_config'].setdefault('templates', {})['AccountReport'] = \
                'dbc_liasse_fiscale.AccountReportFinancementCustomizable'
            # `line.code` is only sent to the client in xlsx/pdf export mode
            # (see account.report._get_static_line_dict), so the custom
            # template can't split table 1 / table 2 lines on it on screen.
            # Table 1's lines are always defined first (sequence 10-70,
            # table 2 starts at 110), so pass their count instead: the
            # template splits controller.lines on this index.
            options['custom_display_config']['ma_financement_table1_count'] = len(
                report.line_ids.filtered(lambda line: (line.code or '').startswith('FIN_SYNTH_'))
            )
