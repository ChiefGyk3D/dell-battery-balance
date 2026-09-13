/*
 * Copyright (C) 2026 ChiefGyk3D
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
import QtQuick
import org.kde.plasma.configuration

ConfigModel {
    ConfigCategory {
        name: i18n("Display")
        icon: "preferences-desktop-display"
        source: "configDisplay.qml"
    }
    ConfigCategory {
        name: i18n("Profiles")
        icon: "battery-profile-powersave"
        source: "configProfiles.qml"
    }
    ConfigCategory {
        name: i18n("Packs")
        icon: "battery"
        source: "configPacks.qml"
    }
    ConfigCategory {
        name: i18n("General")
        icon: "configure"
        source: "configGeneral.qml"
    }
}
