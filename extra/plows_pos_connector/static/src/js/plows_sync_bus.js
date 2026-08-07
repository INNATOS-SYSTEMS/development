/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { formView } from "@web/views/form/form_view";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { onMounted, onWillUnmount } from "@odoo/owl";

export class PlowsSyncFormController extends FormController {
    setup() {
        super.setup();
        this.busService = useService("bus_service");
        this.pollTimer = null;

        onMounted(() => {
            const root = this.model.root;
            if (root && root.resId) {
                const channel = `plows_sync_job_${root.resId}`;
                try {
                    if (this.busService && this.busService.addChannel) {
                        this.busService.addChannel(channel);
                    }
                    if (this.busService && this.busService.addEventListener) {
                        this.busService.addEventListener("notification", this._onNotification.bind(this));
                    }
                } catch (e) {
                    console.warn("[PlowsSync] Error configurando Bus listener:", e);
                }

                // Polling activo constante cada 1.5 segundos mientras la sincronización esté en ejecución (running / queued)
                this.pollTimer = setInterval(async () => {
                    try {
                        const state = this.model.root && this.model.root.data && this.model.root.data.state;
                        if (state === "running" || state === "queued") {
                            await this.model.root.load();
                        }
                    } catch (err) {
                        console.error("[PlowsSync] Error en auto-refresco:", err);
                    }
                }, 1500);
            }
        });

        onWillUnmount(() => {
            if (this.pollTimer) {
                clearInterval(this.pollTimer);
            }
        });
    }

    _onNotification({ detail: notifications }) {
        const root = this.model.root;
        if (!root || !root.resId || !notifications) return;

        for (const notif of notifications) {
            if (notif.type === "plows_sync_update" && notif.payload && notif.payload.job_id === root.resId) {
                root.load();
            }
        }
    }
}

export const plowsSyncFormView = {
    ...formView,
    Controller: PlowsSyncFormController,
};

registry.category("views").add("plows_sync_form", plowsSyncFormView);
