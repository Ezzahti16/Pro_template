import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";
import { patch } from "@web/core/utils/patch";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

import { AccountReportLineCell } from "@account_reports/components/account_report/line_cell/line_cell";

import { Component, useState } from "@odoo/owl";

const CAPITAL_FIELDS = [
    ["nom_prenom", "Nom et prénom", "text"],
    ["raison_sociale", "Raison sociale", "text"],
    ["num_if", "N° IF", "text"],
    ["num_cin", "N° CIN", "text"],
    ["num_ce", "N° Carte d'étranger", "text"],
    ["adresse", "Adresse", "text"],
    ["titres_precedent", "Nombre de titres (exercice précédent)", "number"],
    ["titres_actuel", "Nombre de titres (exercice actuel)", "number"],
    ["valeur_nominale", "Valeur nominale", "number"],
    ["montant_souscrit", "Montant du capital - Souscrit", "number"],
    ["montant_appele", "Montant du capital - Appelé", "number"],
    ["montant_libere", "Montant du capital - Libéré", "number"],
];

export class MaCapitalLinePopover extends Component {
    static template = "dbc_liasse_fiscale.MaCapitalLinePopover";
    static props = {
        line_id: String,
        cell: Object,
        controller: Object,
        onClose: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.fields = CAPITAL_FIELDS;
        const editData = this.props.cell.ma_capital_edit_data;
        const initial = editData ? JSON.parse(editData).values : {};
        this.isCreate = !editData;
        this.state = useState(Object.fromEntries(
            CAPITAL_FIELDS.map(([name, , type]) => [name, initial[name] ?? (type === "number" ? 0 : "")]),
        ));
    }

    async save() {
        const values = Object.fromEntries(
            CAPITAL_FIELDS.map(([name, , type]) => [
                name, type === "number" ? (parseFloat(this.state[name]) || 0) : this.state[name],
            ]),
        );
        const res = await this.orm.call(
            "account.report",
            "action_modify_capital_line",
            [
                this.props.controller.options.report_id,
                this.isCreate ? false : this.props.line_id,
                this.props.controller.options,
                values,
            ],
            { context: this.props.controller.context },
        );

        this.props.controller.lines = res.lines;
        this.props.controller.columnGroupsTotals = res.column_groups_totals;
        this.props.onClose();
    }
}

patch(AccountReportLineCell.prototype, {
    setup() {
        super.setup();
        this.maCapitalOrm = useService("orm");
        this.maCapitalDialog = useService("dialog");
    },

    capitalLinePopover(ev) {
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
            MaCapitalLinePopover,
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

    async capitalLineDelete() {
        this.maCapitalDialog.add(ConfirmationDialog, {
            title: "Supprimer cet associé ?",
            body: "Cette ligne sera définitivement supprimée. Cette action est irréversible.",
            confirm: async () => {
                const res = await this.maCapitalOrm.call(
                    "account.report",
                    "action_delete_capital_line",
                    [this.controller.options.report_id, this.props.line.id, this.controller.options],
                    { context: this.controller.context },
                );
                this.controller.lines = res.lines;
                this.controller.columnGroupsTotals = res.column_groups_totals;
            },
        });
    },
});
