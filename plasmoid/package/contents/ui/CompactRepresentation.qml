/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.components as PlasmaComponents
import org.kde.kirigami as Kirigami

MouseArea {
    id: compact

    // Optional text beside the icon (Display page). The system tray gives
    // every item a square, so this only shows when the widget is placed
    // directly in a panel.
    readonly property string sideText: {
        const mode = Plasmoid.configuration.trayText;
        if (!root.info || mode === 0) return "";
        if (mode === 1) return root.info.profile ? root.info.profile.label : "";
        const d = root.info.divergence_efc;
        return (d === null || d === undefined) ? "" : i18n("Δ%1", d.toFixed(2));
    }

    Layout.minimumWidth: Kirigami.Units.iconSizes.small
        + (label.visible ? label.implicitWidth + Kirigami.Units.smallSpacing : 0)
    Layout.minimumHeight: Kirigami.Units.iconSizes.small
    Layout.preferredWidth: Layout.minimumWidth

    hoverEnabled: true
    onClicked: plasmoid.expanded = !plasmoid.expanded

    RowLayout {
        anchors.fill: parent
        spacing: Kirigami.Units.smallSpacing

        Item {
            id: iconBox
            Layout.fillHeight: true
            Layout.preferredWidth: height

            Kirigami.Icon {
                anchors.fill: parent
                source: Plasmoid.icon
                active: compact.containsMouse
            }

            // Field mode disables the wear protection entirely, and it is
            // easy to leave on by accident. Mark it in the tray, not just
            // in the popup.
            Rectangle {
                visible: root.fieldMode
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: Math.round(parent.width / 3)
                height: width
                radius: width / 2
                color: Kirigami.Theme.negativeTextColor
                border.width: 1
                border.color: Kirigami.Theme.backgroundColor
            }

            // A pack swap left an identity question unanswered. Field
            // mode's red dot takes precedence as the more urgent condition.
            Rectangle {
                visible: root.hasPending && !root.fieldMode
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: Math.round(parent.width / 3)
                height: width
                radius: width / 2
                color: Kirigami.Theme.neutralTextColor
                border.width: 1
                border.color: Kirigami.Theme.backgroundColor
            }
        }

        PlasmaComponents.Label {
            id: label
            visible: compact.sideText !== ""
            text: compact.sideText
            font: Kirigami.Theme.smallFont
            elide: Text.ElideRight
        }
    }
}
