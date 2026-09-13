/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

PlasmaExtras.Representation {
    id: full

    Layout.minimumWidth: Kirigami.Units.gridUnit * 22
    Layout.minimumHeight: Kirigami.Units.gridUnit * 20
    Layout.preferredWidth: Kirigami.Units.gridUnit * 24
    Layout.preferredHeight: Kirigami.Units.gridUnit * 24

    collapseMarginsHint: true

    header: PlasmaExtras.PlasmoidHeading {
        contentItem: RowLayout {
            PlasmaExtras.Heading {
                Layout.fillWidth: true
                level: 4
                text: i18n("Dell Battery Balance")
                elide: Text.ElideRight
            }
            PlasmaComponents.BusyIndicator {
                visible: root.busy || root.acting
                running: visible
                Layout.preferredHeight: Kirigami.Units.iconSizes.small
                Layout.preferredWidth: Layout.preferredHeight
            }
            PlasmaComponents.ToolButton {
                icon.name: "object-align-horizontal-center"
                display: QQC2.AbstractButton.IconOnly
                text: i18n("Balance now")
                enabled: !root.acting
                onClicked: root.act("balance --apply", false)
                PlasmaComponents.ToolTip {
                    text: i18n("Apply the charge ceilings the wear data recommends")
                }
            }
            PlasmaComponents.ToolButton {
                icon.name: "view-refresh"
                display: QQC2.AbstractButton.IconOnly
                text: i18n("Refresh")
                onClicked: root.refresh()
                PlasmaComponents.ToolTip { text: parent.text }
            }
        }
    }

    contentItem: Flickable {
        id: flick
        clip: true
        contentWidth: width
        contentHeight: column.implicitHeight

        ColumnLayout {
            id: column
            width: flick.width
            spacing: Kirigami.Units.smallSpacing

            // ---- config error --------------------------------------------
            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: !!(root.info && root.info.config_error)
                type: Kirigami.MessageType.Error
                text: (root.info && root.info.config_error)
                    ? i18n("Config error: %1", root.info.config_error) : ""
            }

            // ---- field mode warning ------------------------------------
            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: root.fieldMode
                type: Kirigami.MessageType.Warning
                text: i18n("Field mode is on. Both packs charge to 100%, so calendar-wear protection is disabled.")
                actions: [
                    Kirigami.Action {
                        text: i18n("Restore")
                        icon.name: "edit-undo"
                        enabled: !root.acting
                        onTriggered: root.act("restore", false)
                    }
                ]
            }

            Kirigami.InlineMessage {
                Layout.fillWidth: true
                visible: root.lastError !== ""
                type: Kirigami.MessageType.Error
                text: root.lastError
            }

            // ---- per-pack rows -----------------------------------------
            Repeater {
                model: ["BAT0", "BAT1"]

                delegate: ColumnLayout {
                    id: row
                    required property string modelData

                    readonly property var bat: root.info
                        ? root.info.bats[modelData] : null
                    readonly property bool have: bat && bat.present

                    Layout.fillWidth: true
                    Layout.topMargin: Kirigami.Units.smallSpacing
                    spacing: 0
                    visible: have

                    property bool expanded: !!Plasmoid.configuration.expandDetails

                    RowLayout {
                        Layout.fillWidth: true
                        PlasmaExtras.Heading {
                            level: 5
                            text: row.modelData === "BAT0"
                                ? i18n("BAT0 - primary") : i18n("BAT1 - slice")
                        }
                        PlasmaComponents.Label {
                            text: row.have && row.bat.pack ? row.bat.pack : i18n("unidentified")
                            opacity: 0.8
                        }
                        Item { Layout.fillWidth: true }
                        PlasmaComponents.Label {
                            text: row.have
                                ? i18n("%1%  %2", row.bat.capacity, row.bat.status)
                                : ""
                            font.weight: Font.Bold
                        }
                        PlasmaComponents.ToolButton {
                            icon.name: row.expanded ? "arrow-up" : "arrow-down"
                            display: QQC2.AbstractButton.IconOnly
                            text: row.expanded ? i18n("Fewer details") : i18n("More details")
                            onClicked: row.expanded = !row.expanded
                            PlasmaComponents.ToolTip { text: parent.text }
                        }
                    }

                    PlasmaComponents.ProgressBar {
                        Layout.fillWidth: true
                        from: 0
                        to: 100
                        value: row.have ? row.bat.capacity : 0
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: Kirigami.Units.smallSpacing
                        columns: 2
                        rowSpacing: 0
                        columnSpacing: Kirigami.Units.largeSpacing

                        PlasmaComponents.Label {
                            text: i18n("Cycle wear (EFC):")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            text: row.have && row.bat.efc !== null
                                ? row.bat.efc.toFixed(2) : i18n("no data yet")
                        }

                        PlasmaComponents.Label {
                            text: i18n("Calendar stress:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            text: row.have && row.bat.calendar_score !== null
                                ? row.bat.calendar_score.toFixed(1) : i18n("no data yet")
                        }

                        PlasmaComponents.Label {
                            text: i18n("Ceiling:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: {
                                if (!row.have) return "";
                                const fw = (root.info && root.info.firmware) ? root.info.firmware[row.modelData] : null;
                                // What the tool asked for, and what firmware holds now: the live sysfs
                                // read-back when the kernel exposes it for this slot, else the value
                                // observed at the last apply. Drift after the last apply must show as a
                                // mismatch, which is why the live read is preferred.
                                const want = (fw && fw.requested) ? fw.requested : null;
                                const live = (row.bat.start && row.bat.stop) ? [row.bat.start, row.bat.stop] : null;
                                const have = live || ((fw && fw.observed) ? fw.observed : null);
                                const when = live ? i18n("live") : root.ageText(fw ? fw.ts : "");
                                const band = b => i18n("%1 - %2%", b[0], b[1]);
                                if (!want && !have) return i18n("not applied yet");
                                if (!want) return i18n("%1 (firmware, %2)", band(have), when);
                                if (!have) return i18n("%1 - firmware not read back", band(want));
                                const ok = have[0] === want[0] && have[1] === want[1];
                                return ok
                                    ? i18n("%1 - firmware agrees (%2)", band(want), when)
                                    : i18n("%1 - MISMATCH, firmware has %2 (%3)", band(want), band(have), when);
                            }
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        visible: row.expanded && row.have
                        columns: 2
                        rowSpacing: 0
                        columnSpacing: Kirigami.Units.largeSpacing

                        // A nested Repeater-of-Repeater here does not lay out in a
                        // GridLayout: QtQuick.Layouts only makes a directly-declared
                        // child Repeater transparent, not one generated by another
                        // Repeater, so every cell landed at (0, 0). Sixteen explicit
                        // label pairs sidestep that instead of fighting it.
                        PlasmaComponents.Label {
                            text: i18n("Power:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.power_w, 1, i18n(" W")) : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("Voltage:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.voltage_v, 2, i18n(" V")) : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("Temperature:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.temp_c, 1, i18n(" °C")) : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("Health:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: {
                                if (!row.have) return "";
                                return (row.bat.health_pct === null || row.bat.health_pct === undefined)
                                    ? i18n("no data yet")
                                    : i18n("%1% of design (as reported, updates rarely)", row.bat.health_pct.toFixed(1));
                            }
                        }

                        PlasmaComponents.Label {
                            text: i18n("Mean SoC:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.mean_soc, 1, "%") : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("Time at 90%+:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.pct_ge90, 1, "%") : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("Discharged:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: row.have ? root.fmtNum(row.bat.discharged_wh, 1, i18n(" Wh")) : ""
                        }

                        PlasmaComponents.Label {
                            text: i18n("In slot:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            wrapMode: Text.WordWrap
                            text: {
                                if (!row.have) return "";
                                if (row.bat.in_slot_hours === null || row.bat.in_slot_hours === undefined) return i18n("no data yet");
                                return row.bat.in_slot_hours >= 48
                                    ? i18n("%1 d", (row.bat.in_slot_hours / 24).toFixed(1))
                                    : i18n("%1 h", row.bat.in_slot_hours.toFixed(1));
                            }
                        }
                    }

                    Kirigami.InlineMessage {
                        id: pendingMsg
                        Layout.fillWidth: true
                        readonly property var q: row.have ? row.bat.pending : null
                        visible: !!q
                        type: Kirigami.MessageType.Warning
                        text: {
                            const q = pendingMsg.q;
                            if (!q) return "";
                            const why = q.reason === "insert" ? i18n("A pack was inserted") : i18n("The reading jumped");
                            const prev = q.previous_pack ? i18n(" (was %1)", q.previous_pack) : "";
                            const g = q.guess === "same" ? i18n("probably the same pack") : i18n("not sure which pack");
                            return i18n("%1 in %2%3 - %4. Which pack is this?", why, row.modelData, prev, g);
                        }
                        actions: [
                            Kirigami.Action {
                                text: pendingMsg.q && pendingMsg.q.previous_pack ? i18n("Same (%1)", pendingMsg.q.previous_pack) : i18n("Same")
                                icon.name: "dialog-ok"
                                visible: !!(pendingMsg.q && pendingMsg.q.previous_pack)
                                enabled: !root.acting
                                onTriggered: root.act("pack same " + row.modelData, false)
                            }
                        ]
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: row.have && !!row.bat.pending
                        PlasmaComponents.ComboBox {
                            id: knownPacks
                            Layout.fillWidth: true
                            // known, non-retired packs not currently in another slot
                            model: {
                                if (!root.info || !root.info.packs) return [];
                                return root.info.packs
                                    .filter(p => !p.retired && (!p.in_slot || p.in_slot === row.modelData))
                                    .map(p => p.name);
                            }
                            enabled: !root.acting && count > 0
                        }
                        PlasmaComponents.Button {
                            text: i18n("This one")
                            enabled: !root.acting && knownPacks.count > 0
                            onClicked: root.act("pack assign " + row.modelData + " " + knownPacks.currentText, false)
                        }
                        PlasmaComponents.TextField {
                            id: newName
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                            placeholderText: i18n("new name")
                        }
                        PlasmaComponents.Button {
                            text: i18n("New")
                            enabled: !root.acting && root.validPackName(newName.text)
                            onClicked: { root.act("pack new " + row.modelData + " " + newName.text, false); newName.text = ""; }
                        }
                    }
                }
            }

            PlasmaExtras.Heading {
                level: 5
                visible: !!(root.info && root.info.packs && root.info.packs.some(p => !p.in_slot))
                text: i18n("On the bench")
            }
            Repeater {
                model: root.info && root.info.packs ? root.info.packs.filter(p => !p.in_slot) : []
                delegate: PlasmaComponents.Label {
                    required property var modelData
                    Layout.fillWidth: true
                    font: Kirigami.Theme.smallFont
                    opacity: modelData.retired ? 0.5 : 1.0
                    text: modelData.retired
                        ? i18n("%1 - retired, %2 EFC", modelData.name, modelData.efc.toFixed(2))
                        : (modelData.removed_at_soc !== null && modelData.removed_at_soc !== undefined
                            ? i18n("%1 - %2 EFC, out %3 h at %4%", modelData.name, modelData.efc.toFixed(2), Math.round(modelData.bench_hours), modelData.removed_at_soc)
                            : i18n("%1 - %2 EFC", modelData.name, modelData.efc.toFixed(2)))
                }
            }
            PlasmaComponents.Label {
                Layout.fillWidth: true
                visible: !!(root.info && root.info.rotation)
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                text: root.info && root.info.rotation
                    ? i18n("Swap in next: %1 for %2 (%3 EFC behind)", root.info.rotation.swap_in, root.info.rotation.replace, root.info.rotation.behind_by_efc.toFixed(2))
                    : ""
            }

            Kirigami.Separator {
                Layout.fillWidth: true
                Layout.topMargin: Kirigami.Units.smallSpacing
            }

            // ---- summary -----------------------------------------------
            PlasmaComponents.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                text: {
                    if (!root.info) return i18n("Reading...");
                    const d = root.info.divergence_efc;
                    if (d === null || d === undefined) {
                        return i18n("Not enough history to compare the packs yet.");
                    }
                    return i18n("Divergence: %1 EFC cycles, %2 calendar",
                                d.toFixed(2),
                                (root.info.divergence_calendar || 0).toFixed(1));
                }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                opacity: 0.8
                text: {
                    if (!root.info) return "";
                    const f = root.info.drain_first || {};
                    const total = (f.BAT0 || 0) + (f.BAT1 || 0);
                    if (total === 0) {
                        return i18n("EC drain order: not observed yet - unplug to measure");
                    }
                    const lead = (f.BAT1 || 0) >= (f.BAT0 || 0) ? "BAT1" : "BAT0";
                    const n = Math.max(f.BAT0 || 0, f.BAT1 || 0);
                    return i18n("EC reaches for %1 first (%2 of %3 unplugs)",
                                lead, n, total);
                }
            }

            PlasmaComponents.Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                font: Kirigami.Theme.smallFont
                opacity: 0.8
                text: root.info && root.info.recommendation
                    ? root.info.recommendation.why : ""
            }

            PlasmaExtras.Heading {
                level: 5
                Layout.topMargin: Kirigami.Units.smallSpacing
                visible: !!(root.info && root.info.events && root.info.events.length > 0)
                text: i18n("Recent")
            }
            Repeater {
                model: root.info && root.info.events ? root.info.events.slice(-3).reverse() : []
                delegate: PlasmaComponents.Label {
                    required property var modelData
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    font: Kirigami.Theme.smallFont
                    opacity: 0.8
                    text: i18n("%1 - %2: %3", root.ageText(modelData.ts), modelData.kind, modelData.detail)
                }
            }
        }

        PlasmaComponents.ScrollBar.vertical: PlasmaComponents.ScrollBar {}
    }

    footer: PlasmaExtras.PlasmoidHeading {
        position: PlasmaComponents.ToolBar.Footer
        contentItem: ColumnLayout {
            spacing: Kirigami.Units.smallSpacing
            PlasmaComponents.Label {
                readonly property var parts: {
                    const r = root.info ? root.info.revert : null;
                    const out = [];
                    if (!r) return out;
                    if (r.after_hours_left !== null && r.after_hours_left !== undefined)
                        out.push(i18n("%1 h", Math.max(0, r.after_hours_left).toFixed(1)));
                    if (r.on_ac_hours_left !== null && r.on_ac_hours_left !== undefined)
                        out.push(i18n("%1 h on AC", Math.max(0, r.on_ac_hours_left).toFixed(1)));
                    return out;
                }
                visible: text !== ""
                font: Kirigami.Theme.smallFont
                text: {
                    const r = root.info ? root.info.revert : null;
                    if (!r) return "";
                    if (r.stay) return i18n("No automatic revert - stays on %1 until you change it", root.info.profile.label);
                    return parts.length > 0 ? i18n("Reverts to %1 in %2", r.to, parts.join(i18n(" or "))) : "";
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing
                Repeater {
                    model: root.info ? root.info.profiles : []
                    delegate: PlasmaComponents.Button {
                        required property var modelData
                        text: modelData.label
                        checkable: true
                        checked: modelData.active
                        enabled: !root.acting
                        icon.name: modelData.type === "fixed" ? "battery-profile-performance" : "battery-profile-powersave"
                        onClicked: root.act("profile set " + modelData.name, false)
                    }
                }
            }
        }
    }
}
