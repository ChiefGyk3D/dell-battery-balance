/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCM

// Keys (declared in ../config/main.xml, which must stay free of XML
// comments: KConfigLoader silently drops the defaults of every entry that
// follows one -- measured on Plasma 6.3):
//   trayText            0 none, 1 profile label, 2 EFC divergence
//   pollInterval        seconds between `status --json` polls
//   expandDetails       per-pack details open by default
//   notify              raise desktop notifications
//   lastNotifiedEventId highest state event id already notified; -1 = adopt the backlog silently
KCM.SimpleKCM {
    id: page

    property int cfg_trayText
    property int cfg_pollInterval
    property bool cfg_expandDetails
    property bool cfg_notify
    // Declared so the dialog's generic cfg_ round-trip keeps it; never edited here.
    property int cfg_lastNotifiedEventId

    Kirigami.FormLayout {
        QQC2.ComboBox {
            Kirigami.FormData.label: i18n("Text beside the icon:")
            model: [i18n("None"), i18n("Profile label"), i18n("EFC divergence")]
            currentIndex: page.cfg_trayText
            onActivated: page.cfg_trayText = currentIndex
        }
        QQC2.Label {
            text: i18n("Shown when the widget sits in a panel; the system tray keeps it icon-only.")
            font: Kirigami.Theme.smallFont
            opacity: 0.7
        }
        QQC2.SpinBox {
            Kirigami.FormData.label: i18n("Poll interval:")
            from: 10
            to: 600
            stepSize: 5
            value: page.cfg_pollInterval
            onValueModified: page.cfg_pollInterval = value
            textFromValue: (v, locale) => i18n("%1 s", v)
            valueFromText: (t, locale) => parseInt(t) || 30
        }
        QQC2.CheckBox {
            Kirigami.FormData.label: i18n("Pack details:")
            text: i18n("Expanded by default")
            checked: page.cfg_expandDetails
            onToggled: page.cfg_expandDetails = checked
        }
        QQC2.CheckBox {
            Kirigami.FormData.label: i18n("Notifications:")
            text: i18n("Identity questions, reverts, firmware mismatch, pack removed above 70%")
            checked: page.cfg_notify
            onToggled: page.cfg_notify = checked
        }
    }
}
