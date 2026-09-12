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
                icon.name: "view-refresh"
                display: QQC2.AbstractButton.IconOnly
                text: i18n("Refresh")
                onClicked: root.refresh()
                PlasmaComponents.ToolTip { text: parent.text }
            }
        }
    }

    contentItem: Item {
        implicitHeight: column.implicitHeight

        ColumnLayout {
            id: column
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            spacing: Kirigami.Units.smallSpacing

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
                        onTriggered: root.act("restore")
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

                    RowLayout {
                        Layout.fillWidth: true
                        PlasmaExtras.Heading {
                            level: 5
                            text: row.modelData === "BAT0"
                                ? i18n("BAT0 - primary") : i18n("BAT1 - slice")
                        }
                        Item { Layout.fillWidth: true }
                        PlasmaComponents.Label {
                            text: row.have
                                ? i18n("%1%  %2", row.bat.capacity, row.bat.status)
                                : ""
                            font.weight: Font.Bold
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
                            text: i18n("Charge ceiling:")
                            font: Kirigami.Theme.smallFont
                            opacity: 0.8
                        }
                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            font: Kirigami.Theme.smallFont
                            // mode is only readable as root; the band itself
                            // comes back for BAT0 via the power_supply class.
                            text: {
                                if (!row.have) return "";
                                if (row.bat.start && row.bat.stop) {
                                    return i18n("%1 - %2%", row.bat.start, row.bat.stop);
                                }
                                return i18n("run as root to read");
                            }
                        }
                    }
                }
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
        }
    }

    footer: PlasmaExtras.PlasmoidHeading {
        position: PlasmaComponents.ToolBar.Footer
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaComponents.Button {
                Layout.fillWidth: true
                text: i18n("Balance now")
                icon.name: "object-align-horizontal-center"
                enabled: !root.acting
                onClicked: root.act("balance --apply")
                PlasmaComponents.ToolTip {
                    text: i18n("Apply the charge ceilings the wear data recommends")
                }
            }
            PlasmaComponents.Button {
                Layout.fillWidth: true
                text: root.fieldMode ? i18n("Restore") : i18n("Field mode")
                icon.name: root.fieldMode ? "edit-undo" : "battery-profile-performance"
                enabled: !root.acting
                onClicked: root.act(root.fieldMode ? "restore" : "field")
                PlasmaComponents.ToolTip {
                    text: root.fieldMode
                        ? i18n("Return to the wear-balancing ceilings")
                        : i18n("Charge both packs to 100% for maximum runtime")
                }
            }
        }
    }
}
