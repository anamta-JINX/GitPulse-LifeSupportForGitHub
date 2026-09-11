from __future__ import annotations

import importlib
import os
import sys
import unittest


@unittest.skipUnless(os.name == "nt", "Windows tray compatibility test")
class TrayCompatibilityTests(unittest.TestCase):
    def test_tray_imports_when_wintypes_hcursor_is_missing(self) -> None:
        from ctypes import wintypes

        had_hcursor = hasattr(wintypes, "HCURSOR")
        original_hcursor = getattr(wintypes, "HCURSOR", None)

        if had_hcursor:
            delattr(wintypes, "HCURSOR")

        try:
            sys.modules.pop("gitpulse.tray", None)
            tray = importlib.import_module("gitpulse.tray")
            self.assertIs(tray._HCURSOR, wintypes.HANDLE)
        finally:
            if had_hcursor:
                setattr(wintypes, "HCURSOR", original_hcursor)
            sys.modules.pop("gitpulse.tray", None)
            importlib.import_module("gitpulse.tray")


if __name__ == "__main__":
    unittest.main()
