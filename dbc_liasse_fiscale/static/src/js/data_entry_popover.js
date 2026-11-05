import { useService } from "@web/core/utils/hooks";
import { localization } from "@web/core/l10n/localization";
import { patch } from "@web/core/utils/patch";
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

import { AccountReport } from "@account_reports/components/account_report/account_report";
import { AccountReportLineCell } from "@account_reports/components/account_report/line_cell/line_cell";

import { Component, useState } from "@odoo/owl";

/* Live-preview formulas for 'computed' field specs (see
 * _MA_AMORT_FIELDS/_ma_compute_amort_values in models/account_report.py
 * and ma_fiscal_dotation.py for the authoritative, server-side version of
 * the same 3 rules - this copy only drives what the popup shows while the
 * user is still typing, it is never what actually gets saved). Keyed by
 * each computed field's 'compute' spec key, so a future report can add
 * its own entry here without touching any other report's fields.
 */
const MA_COMPUTE_FNS = {
    ma_amort_normal: (state) => {
        const prix = Number(state.prix_acquisition) || 0;
        const anterieurs = Number(state.amort_anterieurs) || 0;
        const valeurComptable = Number(state.valeur_comptable) || 0;
        const duree = Number(state.duree) || 0;
        const brut = prix - anterieurs;
        const plafond = duree ? valeurComptable / duree : 0;
        return brut > plafond ? null : brut;
    },
    ma_amort_total: (state) => {
        const anterieurs = Number(state.amort_anterieurs) || 0;
        const normal = MA_COMPUTE_FNS.ma_amort_normal(state);
        return anterieurs + (normal === null ? 0 : normal);
    },
};

/* Two-way links between a pair of freely-editable fields, keyed by each
 * field's own 'pair_formula' (see taux/duree's pair_field/pair_formula in
 * _MA_AMORT_FIELDS, models/account_report.py): editing either field
 * live-recomputes the other via the formula named here, so they always
 * stay mutually consistent while the user types, in either direction.
 */
const MA_PAIR_FNS = {
    ma_rate_from_duration: (value) => {
        const duree = Number(value) || 0;
        return duree ? 100 / duree : 0;
    },
    ma_duration_from_rate: (value) => {
        const taux = Number(value) || 0;
        return taux ? Math.round(100 / taux) : 0;
    },
};

/* Generic create/edit popup for every Moroccan "data-entry" report
 * (Répartition du Capital Social, État des Emprunts, ...): no EDI code or
 * formula behind any field, so one single component - driven entirely by
 * the field specs the backend sends in options.ma_add_line_button.fields
 * (see _MA_CAPITAL_FIELDS / _MA_LOAN_FIELDS and
 * _init_options_ma_add_line_button in models/account_report.py) - covers
 * every such report without any report-specific JS.
 */
export class MaDataEntryPopover extends Component {
    static template = "dbc_liasse_fiscale.MaDataEntryPopover";
    static props = {
        fields: Array,
        values: { type: Object, optional: true },
        line_id: { type: String, optional: true },
        controller: Object,
        onClose: Function,
        close: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.isCreate = !this.props.line_id;
        const initial = this.props.values || {};
        this.state = useState(Object.fromEntries(
            this.props.fields.map((f) => [f.name, initial[f.name] ?? this._defaultValue(f)]),
        ));
        // Computed fields must reflect the OTHER fields' own current
        // (possibly prefilled, edit-mode) values right away, not only
        // after the user's first keystroke - otherwise the popup would
        // flash a stale 0/blank on open.
        this._recomputeComputedFields();
    }

    _defaultValue(field) {
        // The backend can pin a per-render default (e.g. annee_fiscale is
        // set to whichever year the report's own date filter is currently
        // showing - see _init_options_ma_add_line_button in
        // models/account_report.py) - takes priority over the generic
        // type-based fallbacks below.
        if (field.default !== undefined) {
            return field.default;
        }
        if (field.type === "number" || field.type === "computed") {
            return 0;
        }
        if (field.type === "select") {
            // A <select> with no option explicitly marked selected still
            // visually shows its first option, so the state must default
            // to that same value - otherwise the field looks filled in
            // but saves as empty until the user actually touches it.
            return field.selection?.[0]?.[0] ?? "";
        }
        return "";
    }

    onFieldInput(name, value) {
        this.state[name] = value;

        const field = this.props.fields.find((f) => f.name === name);
        const pairFn = field?.pair_field && MA_PAIR_FNS[field.pair_formula];
        if (pairFn) {
            this.state[field.pair_field] = pairFn(value);
        }

        this._recomputeComputedFields();
    }

    _recomputeComputedFields() {
        for (const field of this.props.fields) {
            const computeFn = field.type === "computed" && MA_COMPUTE_FNS[field.compute];
            if (computeFn) {
                const result = computeFn(this.state);
                this.state[field.name] = result === null ? "" : result;
            }
        }
    }

    _displayComputed(field) {
        const value = this.state[field.name];
        if (value === "" || value === null || value === undefined) {
            return "";
        }
        return field.format === "percent" ? `${Number(value).toFixed(2)}%` : Number(value).toFixed(2);
    }

    async save() {
        const values = Object.fromEntries(
            this.props.fields.map((f) => [
                f.name, f.type === "number" ? (parseFloat(this.state[f.name]) || 0) : this.state[f.name],
            ]),
        );
        const res = await this.orm.call(
            "account.report",
            "action_ma_data_entry_save",
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
        this.maOrm = useService("orm");
        this.maDialog = useService("dialog");
    },

    maEditPopover(ev) {
        if (this.popoverCloseFn) {
            this.popoverCloseFn();
            this.popoverCloseFn = null;
        }

        const close = () => {
            this.popoverCloseFn();
            this.popoverCloseFn = null;
        };

        const editData = JSON.parse(this.props.cell.ma_edit_data);
        const fields = this.controller.options.ma_add_line_button.fields;

        this.popoverCloseFn = this.popover.add(
            ev.currentTarget,
            MaDataEntryPopover,
            {
                fields,
                values: editData.values,
                line_id: this.props.line.id,
                controller: this.controller,
                onClose: close,
            },
            {
                closeOnClickAway: true,
                position: localization.direction === "rtl" ? "bottom" : "left",
            },
        );
    },

    async maDeleteLine() {
        this.maDialog.add(ConfirmationDialog, {
            title: "Supprimer cette ligne ?",
            body: "Cette ligne sera définitivement supprimée. Cette action est irréversible.",
            confirm: async () => {
                const res = await this.maOrm.call(
                    "account.report",
                    "action_ma_data_entry_delete",
                    [this.controller.options.report_id, this.props.line.id, this.controller.options],
                    { context: this.controller.context },
                );
                this.controller.lines = res.lines;
                this.controller.columnGroupsTotals = res.column_groups_totals;
            },
        });
    },
});

patch(AccountReport.prototype, {
    setup() {
        super.setup();
        this.maPopover = useService("popover");
    },

    maAddLine(ev) {
        const conf = this.controller.options.ma_add_line_button;
        if (!conf) {
            return;
        }
        if (this.maPopoverCloseFn) {
            this.maPopoverCloseFn();
            this.maPopoverCloseFn = null;
        }

        const close = () => {
            this.maPopoverCloseFn();
            this.maPopoverCloseFn = null;
        };

        this.maPopoverCloseFn = this.maPopover.add(
            ev.currentTarget,
            MaDataEntryPopover,
            {
                fields: conf.fields,
                values: {},
                controller: this.controller,
                onClose: close,
            },
            {
                closeOnClickAway: true,
                position: "bottom",
            },
        );
    },
});
