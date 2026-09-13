/*
 * Dell Battery Balance - Plasma 6 applet
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.notification as KNotification

PlasmoidItem {
    id: root

    readonly property string cli: "/usr/local/bin/dell-battery-balance"
    readonly property string controlExe: "/usr/local/libexec/dbb-control"
    readonly property string configureExe: "/usr/local/libexec/dbb-configure"

    // Parsed output of `status --json`; null until the first poll returns.
    property var info: null
    property string lastError: ""
    property bool busy: false

    // True while a privileged action is in flight, so the buttons can be
    // disabled rather than queueing a second pkexec prompt behind the first.
    property bool acting: false

    readonly property bool fieldMode: info && info.field_mode === true

    readonly property bool hasPending: !!(info && info.pending && Object.keys(info.pending).length > 0)

    // Pack names must match this before we bother spawning a pkexec prompt
    // for them; the CLI re-validates on the privileged side regardless.
    function validPackName(s) {
        return /^[A-Za-z0-9_-]{1,16}$/.test(s || "");
    }

    // "3 min ago" for an ISO-8601 timestamp; "" when it will not parse.
    function ageText(iso) {
        if (!iso) return "";
        const t = Date.parse(iso);
        if (isNaN(t)) return "";
        const m = Math.round((Date.now() - t) / 60000);
        if (m < 1) return i18n("just now");
        if (m < 60) return i18n("%1 min ago", m);
        const h = Math.round(m / 60);
        if (h < 48) return i18n("%1 h ago", h);
        return i18n("%1 d ago", Math.round(h / 24));
    }

    // A number with a unit, or the shared "no data yet" placeholder.
    function fmtNum(v, dp, unit) {
        if (v === null || v === undefined) return i18n("no data yet");
        return i18n("%1%2", Number(v).toFixed(dp), unit);
    }

    // One Notification object per raised event; autoDelete frees it once
    // shown. The component name matches /usr/share/knotifications6/
    // dell_battery_balance.notifyrc, which install.sh places.
    Component {
        id: notifier
        KNotification.Notification {
            componentName: "dell_battery_balance"
            iconName: "battery-profile-powersave"
            autoDelete: true
        }
    }

    // Map a state event to [notifyrc event id, title]; null when it is
    // not one of the four conditions we notify for (spec §6.3).
    function notifyEventFor(ev) {
        const d = ev.detail || "";
        if (ev.kind === "pack" && d.indexOf("occupancy change") !== -1)
            return ["identityPending", i18n("Which pack is this?")];
        if (ev.kind === "profile" && d.indexOf("(revert:") !== -1)
            return ["revertFired", i18n("Profile reverted")];
        if (ev.kind === "firmware")
            return ["firmwareMismatch", i18n("Firmware disagrees with the requested ceiling")];
        if (ev.kind === "warning")
            return ["packRemovedHigh", i18n("Pack removed at high charge")];
        return null;
    }

    // Root has no session bus, so the applet raises notifications from the
    // events it sees in status. Once per event id: the high-water mark
    // lives in KConfig, so a restart does not re-notify. -1 means this
    // applet instance has never run; adopt the backlog silently.
    function raiseNotifications() {
        if (!info || !info.events) return;
        const evs = info.events.filter(e => typeof e.id === "number");
        if (evs.length === 0) return;
        // Ids restart only if the state file was replaced by hand (reset
        // --all keeps the sequence). Treat a visible high id below the
        // watermark as a fresh state and adopt it silently, like first run.
        const visibleHigh = Math.max(...evs.map(e => e.id));
        let seen = plasmoid.configuration.lastNotifiedEventId;
        if (seen >= 0 && visibleHigh < seen) seen = -1;
        let high = seen;
        for (const ev of evs) {
            if (ev.id <= seen) continue;
            if (ev.id > high) high = ev.id;
            if (seen < 0 || !plasmoid.configuration.notify) continue;
            const m = notifyEventFor(ev);
            if (!m) continue;
            const n = notifier.createObject(root, { eventId: m[0], title: m[1], text: ev.detail });
            n.sendEvent();
        }
        if (high !== seen) plasmoid.configuration.lastNotifiedEventId = high;
    }

    Plasmoid.icon: fieldMode ? "battery-profile-performance"
                             : "battery-profile-powersave"

    toolTipMainText: i18n("Dell Battery Balance")
    toolTipSubText: {
        if (lastError !== "") {
            return lastError;
        }
        if (!info) {
            return i18n("Reading battery state...");
        }
        if (fieldMode) {
            return i18n("Field mode: wear protection is OFF");
        }
        const d = info.divergence_efc;
        return d === null || d === undefined
            ? i18n("No wear history yet")
            : i18n("Wear divergence: %1 EFC", d.toFixed(2));
    }

    Plasma5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []

        onNewData: (source, payload) => {
            disconnectSource(source);
            root.handleResult(source, payload);
        }

        function run(cmd) {
            connectSource(cmd);
        }
    }

    function refresh() {
        busy = true;
        exec.run(cli + " status --json");
    }

    // Privileged actions go through the scoped wrappers so the CLI never
    // runs as root: pkexec --user drops to the dell-battery-balance
    // service account, and the wrapper enforces its own action class.
    function act(subcommand, configure) {
        if (acting) return;
        acting = true;
        const exe = configure ? configureExe : controlExe;
        exec.run("pkexec --user dell-battery-balance " + exe + " " + subcommand);
    }

    function handleResult(source, payload) {
        const stdout = (payload["stdout"] || "").trim();
        const stderr = (payload["stderr"] || "").trim();
        const code = payload["exit code"];

        if (source.indexOf("status --json") !== -1) {
            busy = false;
            if (code !== 0) {
                lastError = stderr !== "" ? stderr : i18n("status failed");
                return;
            }
            try {
                info = JSON.parse(stdout);
                lastError = "";
                raiseNotifications();
            } catch (e) {
                lastError = i18n("Could not parse status output");
            }
            return;
        }

        // A privileged action finished. pkexec exits 126 when the user
        // dismisses the prompt and 127 when authorisation is refused;
        // neither is worth showing as an error.
        acting = false;
        if (code !== 0 && code !== 126 && code !== 127) {
            lastError = stderr !== "" ? stderr : i18n("Action failed (exit %1)", code);
        } else {
            lastError = "";
        }
        refresh();
    }

    Timer {
        interval: Math.max(10, Plasmoid.configuration.pollInterval) * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh()
    }

    compactRepresentation: CompactRepresentation {}
    fullRepresentation: FullRepresentation {}

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: i18n("Refresh now")
            icon.name: "view-refresh"
            onTriggered: root.refresh()
        }
    ]
}
