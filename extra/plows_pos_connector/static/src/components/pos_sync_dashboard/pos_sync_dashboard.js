/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class PlowsPosDashboard extends Component {
    static template = "plows_pos_connector.PlowsPosDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.state = useState({
            loading: true,
            error: null,
            overallHealth: {
                health_code: "healthy",
                health_title: "Cargando...",
                health_message: "",
                last_check_time: "",
                active_warning_count: 0,
            },
            catalogVerifiers: [],
            activeWarnings: [],
            recentClosures: [],
        });

        onWillStart(async () => {
            await this._loadDashboardData();
        });
    }

    async _loadDashboardData() {
        this.state.loading = true;
        this.state.error = null;
        try {
            const data = await this.orm.call(
                "plows.pos.sync.dashboard",
                "get_dashboard_status",
                []
            );
            this.state.overallHealth = data.overall_health || {};
            this.state.catalogVerifiers = data.catalog_verifiers || [];
            this.state.activeWarnings = data.warnings || [];
            this.state.recentClosures = data.recent_closures || [];
        } catch (err) {
            this.state.error = err.message || "Error al conectar con el servidor.";
        } finally {
            this.state.loading = false;
        }
    }

    async onRefresh() {
        await this._loadDashboardData();
    }

    openCatalogAction(verifier) {
        if (verifier.action_xml_id) {
            this.action.doAction(verifier.action_xml_id);
        }
    }

    openWarningTarget(warning) {
        if (warning.target_model && warning.target_res_id) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: warning.target_model,
                res_id: warning.target_res_id,
                views: [[false, "form"]],
                target: "current",
            });
        }
    }

    openClosureTarget(closure) {
        if (closure.closure_id) {
            this.action.doAction({
                type: "ir.actions.act_window",
                res_model: "plows.pos.closure",
                res_id: closure.closure_id,
                views: [[false, "form"]],
                target: "current",
            });
        }
    }
}

registry.category("actions").add("plows_pos_dashboard", PlowsPosDashboard);
registry.category("actions").add("plows_pos_dashboard_tag", PlowsPosDashboard);
