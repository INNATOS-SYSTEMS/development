/** @odoo-module **/

import { registry } from "@web/core/registry";
import { pivotView } from "@web/views/pivot/pivot_view";
import { PivotController } from "@web/views/pivot/pivot_controller";
import { useService } from "@web/core/utils/hooks";

export class PlowsCxcPivotController extends PivotController {
    setup() {
        super.setup();
        this.actionService = useService("action");
        this.orm = useService("orm");
    }

    async onClickSyncReceivables() {
        const action = await this.orm.call("plows.receivable.cxc", "action_sync_receivables", []);
        if (action) {
            await this.actionService.doAction(action);
        }
        await this.model.load();
    }
}

export const plowsCxcPivotView = {
    ...pivotView,
    Controller: PlowsCxcPivotController,
    buttonTemplate: "plows_pos_connector.PlowsCxcPivotView.Buttons",
};

registry.category("views").add("plows_cxc_pivot_view", plowsCxcPivotView);
