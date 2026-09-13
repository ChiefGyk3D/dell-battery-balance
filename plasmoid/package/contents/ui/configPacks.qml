/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

KCM.SimpleKCM {
    id: page

    property var info: null
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    function validPackName(s) {
        return /^[A-Za-z0-9_-]{1,16}$/.test(s || "");
    }

    function where(pk) {
        if (pk.in_slot) return i18n("in %1", pk.in_slot);
        if (pk.retired) return i18n("retired");
        return i18n("on the bench, %1 h", Math.round(pk.bench_hours || 0));
    }

    ConfigBackend {
        id: backend
        onStatusLoaded: i => { page.info = i; }
        onActionDone: { page.message = ""; backend.loadStatus(); }
        onFailed: m => {
            page.message = m;
            page.messageType = Kirigami.MessageType.Error;
        }
        onCancelled: {
            page.message = i18n("Cancelled.");
            page.messageType = Kirigami.MessageType.Information;
        }
    }
    Component.onCompleted: backend.loadStatus()

    ColumnLayout {
        spacing: Kirigami.Units.smallSpacing

        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }
        QQC2.Label {
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
            opacity: 0.7
            font: Kirigami.Theme.smallFont
            text: i18n("Changes on this page take effect immediately; each one asks for authorisation. The two packs are physically identical, so identity is only ever what you confirm here or in the popup.")
        }

        // ---- pending identity questions -------------------------------
        Repeater {
            model: (page.info && page.info.pending)
                ? ["BAT0", "BAT1"].filter(s => !!page.info.pending[s]) : []
            delegate: ColumnLayout {
                id: ask
                required property string modelData
                readonly property var q: page.info.pending[modelData]
                Layout.fillWidth: true

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: true
                    type: Kirigami.MessageType.Warning
                    text: {
                        const why = ask.q.reason === "insert" ? i18n("A pack was inserted") : i18n("The reading jumped");
                        const prev = ask.q.previous_pack ? i18n(" (was %1)", ask.q.previous_pack) : "";
                        const g = ask.q.guess === "same" ? i18n("probably the same pack") : i18n("not sure which pack");
                        return i18n("%1 in %2%3 - %4. Which pack is this?", why, ask.modelData, prev, g);
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    QQC2.Button {
                        visible: !!ask.q.previous_pack
                        text: i18n("Same (%1)", ask.q.previous_pack || "")
                        icon.name: "dialog-ok"
                        enabled: !backend.busy
                        onClicked: backend.act("pack same " + ask.modelData, false)
                    }
                    QQC2.ComboBox {
                        id: known
                        Layout.fillWidth: true
                        model: page.info.packs
                            .filter(pk => !pk.retired && (!pk.in_slot || pk.in_slot === ask.modelData))
                            .map(pk => pk.name)
                        enabled: !backend.busy && count > 0
                    }
                    QQC2.Button {
                        text: i18n("This one")
                        enabled: !backend.busy && known.count > 0
                        onClicked: backend.act("pack assign " + ask.modelData + " " + known.currentText, false)
                    }
                    QQC2.TextField {
                        id: newName
                        Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                        placeholderText: i18n("new name")
                    }
                    QQC2.Button {
                        text: i18n("New")
                        enabled: !backend.busy && page.validPackName(newName.text)
                        onClicked: { backend.act("pack new " + ask.modelData + " " + newName.text, false); newName.text = ""; }
                    }
                }
            }
        }

        Kirigami.Separator { Layout.fillWidth: true }

        // ---- known packs ----------------------------------------------
        QQC2.Label {
            visible: !!(page.info && page.info.packs && page.info.packs.length === 0)
            text: i18n("No packs registered yet. Answer an identity question first.")
            opacity: 0.7
        }
        Repeater {
            model: (page.info && page.info.packs) ? page.info.packs : []
            delegate: RowLayout {
                id: row
                required property var modelData
                Layout.fillWidth: true

                QQC2.Label {
                    text: row.modelData.name
                    font.weight: Font.Bold
                    opacity: row.modelData.retired ? 0.6 : 1.0
                }
                QQC2.Label {
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                    opacity: 0.8
                    text: i18n("%1 EFC, calendar %2, %3",
                               Number(row.modelData.efc).toFixed(2),
                               Number(row.modelData.calendar_score).toFixed(1),
                               page.where(row.modelData))
                }
                QQC2.TextField {
                    id: rename
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 6
                    placeholderText: i18n("rename to")
                }
                QQC2.Button {
                    text: i18n("Rename")
                    icon.name: "edit-rename"
                    enabled: !backend.busy && page.validPackName(rename.text) && rename.text !== row.modelData.name
                    onClicked: { backend.act("pack rename " + row.modelData.name + " " + rename.text, true); rename.text = ""; }
                }
                QQC2.Button {
                    text: row.modelData.retired ? i18n("Unretire") : i18n("Retire")
                    icon.name: row.modelData.retired ? "edit-undo" : "archive-remove"
                    // a pack in a slot cannot be retired (spec §4)
                    enabled: !backend.busy && (row.modelData.retired || !row.modelData.in_slot)
                    onClicked: backend.act((row.modelData.retired ? "pack unretire " : "pack retire ") + row.modelData.name, true)
                }
            }
        }

        QQC2.Button {
            text: i18n("Refresh")
            icon.name: "view-refresh"
            enabled: !backend.busy
            onClicked: backend.loadStatus()
        }
    }
}
