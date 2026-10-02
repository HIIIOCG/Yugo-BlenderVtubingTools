from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_node_studio
load_node_studio()
from yugu_node_studio import outline_bake as addon


material = addon._append_outline_material()
assert material.name.startswith(addon.OUTLINE_MATERIAL_NAME), material.name
assert material.library is None
assert material.users == 0
bpy.data.materials.remove(material)

print("YUGO_OUTLINE_PRESET_TEST_OK")
