"""Static checks on the Plasma applet package.

Two mistakes that only showed up on the desktop, never in plasmawindowed:
XML comments inside main.xml make KConfigLoader drop the defaults of every
entry after them (the poll interval became NaN and notifications never
fired), and `plasmoid.expanded` is not a property in Plasma 6 (clicking the
icon never opened the popup). Both are cheap to catch here."""
import os
import re
import unittest
import xml.etree.ElementTree as ET

PKG = os.path.join(os.path.dirname(__file__), os.pardir, "plasmoid", "package")
UI = os.path.join(PKG, "contents", "ui")
MAIN_XML = os.path.join(PKG, "contents", "config", "main.xml")
CONFIG_QML = os.path.join(PKG, "contents", "config", "config.qml")


def qml_files():
    return sorted(f for f in os.listdir(UI) if f.endswith(".qml"))


class ConfigSchema(unittest.TestCase):
    def test_no_xml_comments_anywhere(self):
        with open(MAIN_XML) as fh:
            self.assertNotIn("<!--", fh.read())

    def test_every_entry_has_a_typed_default(self):
        ns = {"k": "http://www.kde.org/standards/kcfg/1.0"}
        entries = ET.parse(MAIN_XML).getroot().findall(".//k:entry", ns)
        self.assertGreaterEqual(len(entries), 5)
        for e in entries:
            with self.subTest(entry=e.get("name")):
                self.assertIn(e.get("type"), ("Int", "Bool", "String", "Double"))
                self.assertIsNotNone(e.find("k:default", ns), "missing <default>")

    def test_every_key_the_qml_reads_is_declared(self):
        ns = {"k": "http://www.kde.org/standards/kcfg/1.0"}
        declared = {e.get("name") for e in ET.parse(MAIN_XML).getroot().findall(".//k:entry", ns)}
        used = set()
        for f in qml_files():
            with open(os.path.join(UI, f)) as fh:
                used.update(re.findall(r"\b[Pp]lasmoid\.configuration\.([A-Za-z_]+)", fh.read()))
        self.assertEqual(used - declared, set(), "QML reads keys main.xml does not declare")

    def test_config_pages_exist(self):
        with open(CONFIG_QML) as fh:
            sources = re.findall(r'source:\s*"([^"]+)"', fh.read())
        self.assertGreaterEqual(len(sources), 4)
        for s in sources:
            with self.subTest(page=s):
                self.assertTrue(os.path.exists(os.path.join(UI, s)))


class PlasmaSixIdioms(unittest.TestCase):
    def test_expansion_is_toggled_on_the_root_item(self):
        for f in qml_files():
            with open(os.path.join(UI, f)) as fh:
                self.assertNotIn("plasmoid.expanded", fh.read(), f)

    def test_every_qml_file_carries_the_license_header(self):
        for f in qml_files():
            with open(os.path.join(UI, f)) as fh:
                head = fh.read(400)
            self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", head, f)


if __name__ == "__main__":
    unittest.main()
