#!/usr/bin/env python3
"""Launcher for the fleet GUI: python3 gui/server.py [--demo] [--port N].

Same as `python3 -m fleet_gui` run from this directory. See README.md.
"""

from fleet_gui import __main__ as fleet_gui_main

if __name__ == "__main__":
    fleet_gui_main.main()
