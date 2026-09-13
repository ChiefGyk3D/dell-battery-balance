/*
 * Dell Battery Balance - Plasma 6 applet
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasma5support as Plasma5Support

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
        interval: 30000
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
