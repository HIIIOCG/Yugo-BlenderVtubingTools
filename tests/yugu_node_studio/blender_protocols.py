"""Run the parser and native receiver snapshots with Blender mathutils."""
from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()
from test_protocols import ProtocolTests

suite = unittest.defaultTestLoader.loadTestsFromTestCase(ProtocolTests)
assert suite.countTestCases() == 6
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful() and not result.skipped
print("YUGUS_NATIVE_PROTOCOLS_OK")
