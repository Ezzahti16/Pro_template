import { useAutofocus, useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";
import { patch } from "@web/core/utils/patch";

import { AccountReportLineCell } from "@account_reports/components/account_report/line_cell/line_cell";

import { Component, useRef } from "@odoo/owl";

export class MaFormulaEditPopover extends Component {
    static template = "dbc_liasse_fiscale.MaFormulaEditPopover";
    static props = {
        line_id: String,
        cell: Object,
        controller: Object,
        onClose: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.input = useRef("input");
        useAutofocus({ refName: "input" });
    }

    get editData() {
        return JSON.parse(this.props.cell.ma_formula_edit_data);
    }

    async save() {
        const editData = this.editData;
        const res = await this.orm.call(
            "account.report",
            "action_modify_formula",
            [
                this.props.controller.options.report_id,
                this.props.line_id,
                this.props.controller.options,
                editData.column_group_key,
                editData.edi_code,
                this.input.el.value,
            ],
            {
                context: this.props.controller.context,
            }
        );

        this.props.controller.lines = res.lines;
        this.props.controller.columnGroupsTotals = res.column_groups_totals;

        this.props.onClose();
    }
}

patch(AccountReportLineCell.prototype, {
    formulaEditPopover(ev) {
        if (this.popoverCloseFn) {
            this.popoverCloseFn();
            this.popoverCloseFn = null;
        }

        const close = () => {
            this.popoverCloseFn();
            this.popoverCloseFn = null;
        };

        this.popoverCloseFn = this.popover.add(
            ev.currentTarget,
            MaFormulaEditPopover,
            {
                line_id: this.props.line.id,
                cell: this.props.cell,
                controller: this.controller,
                onClose: close,
            },
            {
                closeOnClickAway: true,
                position: localization.direction === "rtl" ? "bottom" : "left",
            },
        );
    },
});
