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

    property var cfg: null                 // working copy of the whole config
    property string sel: ""                // selected profile name
    readonly property var names: cfg ? Object.keys(cfg.profiles).sort() : []
    readonly property var p: (cfg && sel && cfg.profiles[sel]) ? cfg.profiles[sel] : null
    readonly property bool fixed: !!(p && p.bands && ("all" in p.bands))
    property string message: ""
    property int messageType: Kirigami.MessageType.Information

    signal configurationChanged()

    function saveConfig() {
        if (cfg) backend.apply(cfg);
    }

    // Edits mutate the plain JS object; re-emit so bindings on page.cfg /
    // page.p re-evaluate, and arm the dialog's Apply button.
    function touch() {
        page.cfgChanged();
        page.configurationChanged();
    }

    function validNewName(s) {
        return /^[a-z0-9_-]{1,32}$/.test(s || "") && !(cfg && (s in cfg.profiles));
    }

    // The picker's currentIndex binding breaks on the first user selection
    // (standard ComboBox behaviour), so keep it in sync by hand. The editor
    // is re-created per selection so every control starts from a fresh
    // binding to the newly selected profile -- keyed on `sel` (a string)
    // rather than on `p`, because a var property signals change on every
    // re-evaluation, which touch() forces after each edit. The active
    // check reads `cfg`/`sel` directly rather than the derived `p`: inside
    // this very handler `p`'s own binding has not yet caught up with the
    // `sel` change that triggered it (it still reflects the previous
    // selection for one more read), which would leave the editor
    // uncreated on the very first selection after load.
    onSelChanged: {
        picker.currentIndex = names.indexOf(sel);
        editor.active = false;
        editor.active = !!(cfg && sel && cfg.profiles[sel]);
    }
    onNamesChanged: picker.currentIndex = names.indexOf(sel)

    ConfigBackend {
        id: backend
        onLoaded: c => { page.cfg = c; page.sel = c.general.active_profile; page.message = ""; }
        onApplied: {
            page.message = i18n("Profiles applied.");
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

    // One [start, stop] editor. key is "all" | "neutral" | "protect" | "work".
    // The from/to limits are bindings (not broken by interaction), so the
    // user cannot cross start over stop.
    component BandRow: RowLayout {
        id: band
        required property string key
        readonly property var v: (page.p && page.p.bands && page.p.bands[band.key])
            ? page.p.bands[band.key] : [50, 80]
        QQC2.SpinBox {
            from: 50
            to: Math.min(95, band.v[1] - 1)
            value: band.v[0]
            onValueModified: { page.p.bands[band.key] = [value, band.v[1]]; page.touch(); }
        }
        QQC2.Label { text: i18n("to") }
        QQC2.SpinBox {
            from: Math.max(55, band.v[0] + 1)
            to: 100
            value: band.v[1]
            onValueModified: { page.p.bands[band.key] = [band.v[0], value]; page.touch(); }
        }
        QQC2.Label { text: "%" }
    }

    // Per-slot pin: follow the wear logic, pin a role, or pin a fixed band.
    component PinRow: RowLayout {
        id: pin
        required property string slot
        readonly property var cur: (page.p && page.p.pins && page.p.pins[pin.slot]) ? page.p.pins[pin.slot] : null
        readonly property int mode: !cur ? 0
            : (cur.role ? ["neutral", "protect", "work"].indexOf(cur.role) + 1 : 4)
        QQC2.ComboBox {
            model: [i18n("Follow the wear logic"), i18n("Pin role: neutral"),
                    i18n("Pin role: protect"), i18n("Pin role: work"), i18n("Pin a fixed band")]
            currentIndex: pin.mode
            onActivated: idx => {
                if (idx === 0) {
                    if (page.p.pins) {
                        delete page.p.pins[pin.slot];
                        if (Object.keys(page.p.pins).length === 0) delete page.p.pins;
                    }
                } else {
                    if (!page.p.pins) page.p.pins = {};
                    page.p.pins[pin.slot] = idx === 4
                        ? { start: 55, stop: 70 }
                        : { role: ["neutral", "protect", "work"][idx - 1] };
                }
                page.touch();
            }
        }
        QQC2.SpinBox {
            visible: pin.mode === 4
            from: 50
            to: pin.cur && pin.cur.stop !== undefined ? Math.min(95, pin.cur.stop - 1) : 95
            value: pin.cur && pin.cur.start !== undefined ? pin.cur.start : 55
            onValueModified: { page.p.pins[pin.slot] = { start: value, stop: pin.cur.stop }; page.touch(); }
        }
        QQC2.Label { visible: pin.mode === 4; text: i18n("to") }
        QQC2.SpinBox {
            visible: pin.mode === 4
            from: pin.cur && pin.cur.start !== undefined ? Math.max(55, pin.cur.start + 1) : 55
            to: 100
            value: pin.cur && pin.cur.stop !== undefined ? pin.cur.stop : 70
            onValueModified: { page.p.pins[pin.slot] = { start: pin.cur.start, stop: value }; page.touch(); }
        }
        QQC2.Label { visible: pin.mode === 4; text: "%" }
    }

    ColumnLayout {
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: page.message !== ""
            type: page.messageType
            text: page.message
        }

        Kirigami.FormLayout {
            id: top
            enabled: !!page.cfg && !backend.busy

            QQC2.ComboBox {
                id: picker
                Kirigami.FormData.label: i18n("Profile:")
                model: page.names
                onActivated: idx => page.sel = page.names[idx]
            }
            RowLayout {
                Kirigami.FormData.label: i18n("New profile:")
                QQC2.TextField {
                    id: newName
                    Layout.preferredWidth: Kirigami.Units.gridUnit * 8
                    placeholderText: i18n("name: a-z 0-9 _ -")
                }
                QQC2.Button {
                    text: i18n("Add as a copy of %1", page.sel)
                    icon.name: "list-add"
                    enabled: !!page.p && page.validNewName(newName.text)
                    onClicked: {
                        const copy = JSON.parse(JSON.stringify(page.p));
                        copy.label = newName.text;
                        page.cfg.profiles[newName.text] = copy;
                        const n = newName.text;
                        newName.text = "";
                        page.touch();
                        page.sel = n;
                    }
                }
            }
            QQC2.Button {
                text: i18n("Delete %1", page.sel)
                icon.name: "edit-delete"
                // daily is the fallback and the active profile is in use (spec §1.2, §4)
                enabled: !!page.p && page.sel !== "daily"
                    && page.sel !== page.cfg.general.active_profile
                onClicked: {
                    // a revert target that disappears falls back to the previous profile,
                    // as the tool itself does for previous_profile
                    for (const n of Object.keys(page.cfg.profiles)) {
                        const r = page.cfg.profiles[n].revert;
                        if (r && r.to === page.sel) r.to = "previous";
                    }
                    // the CLI's profile delete does the same reset
                    if (page.cfg.general.previous_profile === page.sel)
                        page.cfg.general.previous_profile = "daily";
                    delete page.cfg.profiles[page.sel];
                    page.sel = "daily";
                    page.touch();
                }
            }
        }

        Loader {
            id: editor
            Layout.fillWidth: true
            active: false
            sourceComponent: editorForm
        }
    }

    Component {
        id: editorForm

        Kirigami.FormLayout {
            enabled: !!page.p && !backend.busy

            Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: page.sel }

            QQC2.TextField {
                Kirigami.FormData.label: i18n("Label:")
                text: page.p ? page.p.label : ""
                onTextEdited: { page.p.label = text; page.touch(); }
            }
            QQC2.TextField {
                Kirigami.FormData.label: i18n("Description:")
                text: page.p ? (page.p.description || "") : ""
                onTextEdited: { page.p.description = text; page.touch(); }
            }
            QQC2.ComboBox {
                Kirigami.FormData.label: i18n("Type:")
                model: [i18n("Balancing - the wear logic picks each pack's band"),
                        i18n("Fixed - one band for both packs")]
                currentIndex: page.fixed ? 1 : 0
                onActivated: idx => {
                    if ((idx === 1) === page.fixed) return;
                    if (idx === 1) {
                        page.p.bands = { all: [50, 80] };
                        page.p.balancing = false;
                        delete page.p.pins;
                    } else {
                        page.p.bands = { neutral: [50, 80], protect: [50, 60], work: [80, 90] };
                        page.p.balancing = true;
                    }
                    page.touch();
                }
            }

            BandRow { Kirigami.FormData.label: i18n("Both packs:"); visible: page.fixed; key: "all" }
            BandRow { Kirigami.FormData.label: i18n("Neutral band:"); visible: !page.fixed; key: "neutral" }
            BandRow { Kirigami.FormData.label: i18n("Protect band:"); visible: !page.fixed; key: "protect" }
            BandRow { Kirigami.FormData.label: i18n("Work band:"); visible: !page.fixed; key: "work" }

            Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: i18n("Auto-revert") }

            QQC2.CheckBox {
                id: revertBox
                Kirigami.FormData.label: i18n("Revert:")
                text: i18n("Switch back automatically")
                checked: !!(page.p && page.p.revert)
                onToggled: {
                    if (checked) page.p.revert = { after_hours: 72, to: "previous" };
                    else delete page.p.revert;
                    page.touch();
                }
            }
            QQC2.SpinBox {
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("After:")
                from: (page.p && page.p.revert && page.p.revert.on_ac_hours) ? 0 : 1
                to: 720
                value: (page.p && page.p.revert && page.p.revert.after_hours) ? page.p.revert.after_hours : 72
                textFromValue: (v, locale) => v === 0 ? i18n("never") : i18n("%1 h", v)
                valueFromText: (t, locale) => parseInt(t) || 0
                onValueModified: {
                    if (value > 0) page.p.revert.after_hours = value;
                    else delete page.p.revert.after_hours;
                    page.touch();
                }
            }
            QQC2.SpinBox {
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("After on AC for:")
                from: 0
                to: 168
                value: (page.p && page.p.revert && page.p.revert.on_ac_hours) ? page.p.revert.on_ac_hours : 0
                textFromValue: (v, locale) => v === 0 ? i18n("never") : i18n("%1 h", v)
                valueFromText: (t, locale) => parseInt(t) || 0
                onValueModified: {
                    if (value > 0) {
                        page.p.revert.on_ac_hours = value;
                    } else {
                        delete page.p.revert.on_ac_hours;
                        // a revert with no trigger is rejected by the tool
                        if (!page.p.revert.after_hours) page.p.revert.after_hours = 72;
                    }
                    page.touch();
                }
            }
            QQC2.ComboBox {
                id: revertTo
                visible: revertBox.checked
                Kirigami.FormData.label: i18n("Revert to:")
                model: [i18n("the previous profile")].concat(page.names.filter(n => n !== page.sel))
                currentIndex: {
                    const to = (page.p && page.p.revert) ? page.p.revert.to : "previous";
                    if (to === "previous") return 0;
                    const i = page.names.filter(n => n !== page.sel).indexOf(to);
                    return i < 0 ? 0 : i + 1;
                }
                onActivated: idx => {
                    page.p.revert.to = idx === 0 ? "previous" : page.names.filter(n => n !== page.sel)[idx - 1];
                    page.touch();
                }
            }

            Kirigami.Separator {
                visible: !page.fixed
                Kirigami.FormData.isSection: true
                Kirigami.FormData.label: i18n("Pins (override the wear logic)")
            }
            PinRow { Kirigami.FormData.label: "BAT0:"; visible: !page.fixed; slot: "BAT0" }
            PinRow { Kirigami.FormData.label: "BAT1:"; visible: !page.fixed; slot: "BAT1" }
        }
    }
}
