/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.kirigami as Kirigami

MouseArea {
    id: compact

    // The tray gives us a square; keep the icon square inside it.
    Layout.minimumWidth: Kirigami.Units.iconSizes.small
    Layout.minimumHeight: Kirigami.Units.iconSizes.small

    hoverEnabled: true
    onClicked: plasmoid.expanded = !plasmoid.expanded

    Kirigami.Icon {
        id: icon
        anchors.fill: parent
        source: Plasmoid.icon
        active: compact.containsMouse
    }

    // Field mode disables the wear protection entirely, and it is easy to
    // leave on by accident. Mark it in the tray, not just in the popup.
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

    // A pack swap left an identity question unanswered. Field mode's red
    // dot takes precedence since it is the more urgent condition.
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
