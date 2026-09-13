/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.plasma5support as Plasma5Support

// Shared by the config pages: read the live config or status as JSON, run a
// privileged subcommand, or apply a replacement config through the
// configure-class polkit action. Each call is one shell command through the
// executable engine, so the pages stay declarative.
Item {
    id: backend

    property string cli: "/usr/local/bin/dell-battery-balance"
    property string controlExe: "/usr/local/libexec/dbb-control"
    property string configureExe: "/usr/local/libexec/dbb-configure"
    // Overridable so the pipeline can be exercised unprivileged (see tools/).
    property string privPrefix: "pkexec --user dell-battery-balance "
    property string applyVerb: "config apply --json"
    property bool busy: false

    signal loaded(var cfg)
    signal statusLoaded(var info)
    signal applied()
    signal actionDone(string source)
    signal failed(string message)
    signal cancelled()

    Plasma5Support.DataSource {
        id: exec
        engine: "executable"
        connectedSources: []
        onNewData: (source, payload) => {
            disconnectSource(source);
            backend.busy = false;
            backend.handle(source, payload);
        }
    }

    function run(cmd) {
        busy = true;
        exec.connectSource(cmd);
    }

    function load() { run(cli + " config get --json"); }
    function loadStatus() { run(cli + " status --json"); }

    function act(subcommand, configure) {
        run(privPrefix + (configure ? configureExe : controlExe) + " " + subcommand);
    }

    // The service account cannot read $XDG_RUNTIME_DIR (mode 700), so the
    // candidate goes through a mktemp file in /tmp, world-readable for the
    // seconds it exists. It carries no secrets: the config holds bands and
    // the *path* to a BIOS password file, never a password. base64 keeps
    // the JSON out of shell quoting entirely (Qt.btoa encodes UTF-8).
    function apply(cfg) {
        const b64 = Qt.btoa(JSON.stringify(cfg));
        run("sh -c 'f=$(mktemp /tmp/dbb-config-XXXXXX.json) && printf %s " + b64
            + " | base64 -d > \"$f\" && chmod 644 \"$f\" && "
            + privPrefix + configureExe + " " + applyVerb + " \"$f\"; "
            + "rc=$?; rm -f -- \"$f\"; exit $rc'");
    }

    function handle(source, payload) {
        const stdout = (payload["stdout"] || "").trim();
        const stderr = (payload["stderr"] || "").trim();
        const code = payload["exit code"];
        const isGet = source.indexOf(" config get --json") !== -1;
        const isStatus = source.indexOf(" status --json") !== -1;
        if (isGet || isStatus) {
            if (code !== 0) {
                failed(stderr !== "" ? stderr : i18n("could not read the tool's output"));
                return;
            }
            try {
                const parsed = JSON.parse(stdout);
                if (isStatus) statusLoaded(parsed); else loaded(parsed);
            } catch (e) {
                failed(i18n("could not parse the tool's output"));
            }
            return;
        }
        // pkexec exits 126 when the prompt is dismissed and 127 when
        // authorisation is refused; neither is an error to show.
        if (code === 126 || code === 127) { cancelled(); return; }
        if (code !== 0) {
            failed(stderr !== "" ? stderr : i18n("action failed (exit %1)", code));
            return;
        }
        if (source.indexOf("dbb-config-") !== -1) applied(); else actionDone(source);
    }
}
