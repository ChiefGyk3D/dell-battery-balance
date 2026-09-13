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

    // Working copy of the whole config: `config apply` replaces everything,
    // so even a one-field change submits the full document.
    property var cfg: null
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    // The dialog connects this to its Apply button.
    signal configurationChanged()

    // Called by the dialog on Apply / OK, after it wrote KConfig. The
    // result arrives asynchronously; use Apply (not OK) to see a
    // validation error, since OK closes the dialog.
    function saveConfig() {
        if (cfg) backend.apply(cfg);
    }

    function touch() {
        page.cfgChanged();
        page.configurationChanged();
    }

    ConfigBackend {
        id: backend
        onLoaded: c => { page.cfg = c; page.message = ""; }
        onApplied: {
            page.message = i18n("Applied.");
            page.messageType = Kirigami.MessageType.Positive;
        }
        // re-arm Apply: the dialog disabled it when saveConfig() ran, before this result arrived
        onFailed: m => {
            page.message = m;
            page.messageType = Kirigami.MessageType.Error;
            page.configurationChanged();
        }
        onCancelled: {
            page.message = i18n("Not applied: authorisation was cancelled.");
            page.messageType = Kirigami.MessageType.Information;
            page.configurationChanged();
        }
    }
    Component.onCompleted: backend.load()

    ColumnLayout {
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }

        Kirigami.FormLayout {
            enabled: !!page.cfg && !backend.busy

            QQC2.SpinBox {
                Kirigami.FormData.label: i18n("Balance deadband:")
                // hundredths of an EFC; SpinBox is integer-only
                from: 0
                to: 500
                stepSize: 5
                value: page.cfg ? Math.round(page.cfg.general.deadband_efc * 100) : 50
                textFromValue: (v, locale) => i18n("%1 EFC", (v / 100).toFixed(2))
                valueFromText: (t, locale) => Math.round(parseFloat(t) * 100) || 0
                onValueModified: { page.cfg.general.deadband_efc = value / 100; page.touch(); }
            }
            QQC2.Label {
                text: i18n("Below this EFC gap the packs are treated as even and both get the neutral band.")
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            QQC2.CheckBox {
                Kirigami.FormData.label: i18n("Automatic balancing:")
                text: i18n("Apply the recommended ceilings on every tick")
                checked: page.cfg ? page.cfg.general.auto_balance : true
                onToggled: { page.cfg.general.auto_balance = checked; page.touch(); }
            }
            QQC2.SpinBox {
                Kirigami.FormData.label: i18n("Bench temperature:")
                from: -20
                to: 60
                value: page.cfg ? Math.round(page.cfg.general.bench_temp_c) : 25
                textFromValue: (v, locale) => i18n("%1 °C", v)
                valueFromText: (t, locale) => parseInt(t) || 25
                onValueModified: { page.cfg.general.bench_temp_c = value; page.touch(); }
            }
            QQC2.Label {
                text: i18n("Assumed temperature for packs on the shelf; it drives the bench calendar-wear estimate.")
                font: Kirigami.Theme.smallFont
                opacity: 0.7
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }
    }
}
