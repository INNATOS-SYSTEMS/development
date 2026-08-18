/** @odoo-module **/

import { registry } from "@web/core/registry";
import { listView } from "@web/views/list/list_view";
import { ListController } from "@web/views/list/list_controller";
import { useService } from "@web/core/utils/hooks";

export class PlowsCxcListController extends ListController {
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

export const plowsCxcListView = {
    ...listView,
    Controller: PlowsCxcListController,
    buttonTemplate: "plows_pos_connector.PlowsCxcListView.Buttons",
};

registry.category("views").add("plows_cxc_list_view", plowsCxcListView);
