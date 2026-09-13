"""Every command the README documents must parse, and every command the
parser knows must be documented. This is what makes `profile field`-style
surprises a test failure instead of a bug report."""
import argparse
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

README = os.path.join(os.path.dirname(__file__), os.pardir, "README.md")

# One entry per documented form, placeholders filled with plausible values.
DOCUMENTED = [
    ["tick"], ["sample"], ["status"], ["status", "--json"], ["status", "--prometheus"], ["report"],
    ["balance"], ["balance", "--apply"],
    ["profile", "list"], ["profile", "show", "daily"],
    ["profile", "set", "field"], ["profile", "set", "field", "--for", "8h"],
    ["profile", "set", "field", "--stay"], ["profile", "field"],
    ["profile", "create", "trip", "--from", "travel"],
    ["profile", "edit", "field", "revert.after_hours=120"], ["profile", "edit", "field", "revert=none"],
    ["profile", "delete", "trip"],
    ["config", "get"], ["config", "get", "general.deadband_efc"], ["config", "get", "--json"],
    ["config", "set", "general.deadband_efc=0.4"],
    ["config", "validate", "x.toml"], ["config", "validate", "--json", "x.json"],
    ["config", "apply", "x.toml"], ["config", "apply", "--json", "x.json"],
    ["field"], ["field", "--for", "3d"], ["field", "--stay"], ["restore"],
    ["pack", "list"], ["pack", "assign", "BAT0", "A"], ["pack", "new", "BAT0", "A"],
    ["pack", "same", "BAT0"], ["pack", "reassign", "3", "A"], ["pack", "rename", "A", "B"],
    ["pack", "retire", "A"], ["pack", "unretire", "A"], ["pack", "swap"],
    ["reset", "--slot", "BAT0"], ["reset", "--pack", "A"], ["reset", "--all"],
    ["--polkit-class", "control", "--", "profile", "field"],
]

ROW = re.compile(r"^\| `([^`]+)`")
GROUPS = ("profile", "config", "pack")


def _subparser_choices(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def parser_commands(parser):
    """{('tick',), ('profile', 'set'), ...} straight from argparse."""
    out = set()
    for name, sub in _subparser_choices(parser).items():
        inner = _subparser_choices(sub)
        if inner:
            out.update((name, k) for k in inner)
        else:
            out.add((name,))
    return out


def readme_commands():
    with open(README) as fh:
        text = fh.read()
    section = text.split("### CLI reference", 1)[1].split("\n## ", 1)[0]
    out = set()
    for line in section.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        words = []
        for w in m.group(1).split():
            if w[0] in "<[-\\|" or "=" in w:
                break
            words.append(w)
        out.add(tuple(words[:2]) if words[0] in GROUPS else (words[0],))
    return out


class CommandSurface(unittest.TestCase):
    def setUp(self):
        for m in list(sys.modules):
            if m.startswith("dbb"):
                del sys.modules[m]
        from dbb import cli
        self.cli = cli
        self.parser = cli.build_parser()

    def test_every_documented_form_parses(self):
        for argv in DOCUMENTED:
            with self.subTest(argv=" ".join(argv)):
                try:
                    self.parser.parse_args(self.cli.expand_profile_shortcut(argv))
                except SystemExit as e:
                    self.fail(f"{' '.join(argv)!r} does not parse (exit {e.code})")

    def test_readme_table_matches_the_parser(self):
        documented = readme_commands()
        known = parser_commands(self.parser)
        # `profile <name>` is the shortcut row; it is not a subparser
        self.assertEqual(documented - known - {("profile",)}, set(),
                         "README documents commands the parser does not have")
        self.assertEqual(known - documented, set(),
                         "parser has commands the README does not document")
