"""Compatibility runner for the current recipient batching contract."""
import runpy
from pathlib import Path
import unittest
import sys
ns = runpy.run_path(str(Path(__file__).with_name("test-recipient-batching.py")))
if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(ns["HubFlowTests"]))
    sys.exit(not result.wasSuccessful())
