from __future__ import annotations

import struct
import unittest
from pathlib import Path
import sys
from importlib import import_module


sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import load_protocol_source
SOURCE = load_protocol_source()
ARKIT_BLENDSHAPES = import_module(f"{SOURCE}.constants").ARKIT_BLENDSHAPES
parser = import_module(f"{SOURCE}.protocols")
VmcTransform = parser.VmcTransform
canonical_ifacial_name = parser.canonical_ifacial_name
decode_osc_packet = parser.decode_osc_packet
ifacial_expression_aliases = parser.ifacial_expression_aliases
parse_ifacial_frame = parser.parse_ifacial_frame
parse_vmc_expression = parser.parse_vmc_expression
try:
    import mathutils
except ImportError:
    HAS_MATHUTILS = False
else:
    HAS_MATHUTILS = True
    receiver_source = import_module(f"{SOURCE}.network")
    IFacialReceiver = receiver_source.IFacialReceiver
    VMCReceiver = receiver_source.VMCReceiver


def osc_string(value):
    encoded = value.encode("utf-8") + b"\0"
    return encoded + b"\0" * ((-len(encoded)) % 4)


class ProtocolTests(unittest.TestCase):
    def test_vmc_expression(self):
        packet = osc_string("/VMC/Ext/Blend/Val") + osc_string(",sf")
        packet += osc_string("Blink_L") + struct.pack(">f", 0.75)
        messages = decode_osc_packet(packet)
        self.assertEqual(len(messages), 1)
        name, value = parse_vmc_expression(messages[0])
        self.assertEqual(name, "Blink_L")
        self.assertAlmostEqual(value, 0.75)

    def test_ifacial(self):
        expressions, head = parse_ifacial_frame(
            "eyeBlink_L&25|mouthSmile_R-75|=head#10,20,-30"
        )
        self.assertEqual(expressions["eyeBlinkLeft"], 25.0)
        self.assertEqual(expressions["mouthSmileRight"], 75.0)
        self.assertEqual(head, (10.0, 20.0, -30.0))

    def test_ifacial_parses_all_standard_keys_and_negative_v2_values(self):
        fields = []
        for index, name in enumerate(ARKIT_BLENDSHAPES):
            if name.endswith("Left"):
                wire_name = name[:-4] + "_L"
            elif name.endswith("Right"):
                wire_name = name[:-5] + "_R"
            else:
                wire_name = name
            value = -0.5 if index == 0 else float(index)
            fields.append(f"{wire_name}&{value}")
        expressions, _head = parse_ifacial_frame("|".join(fields))
        self.assertEqual(set(expressions), set(ARKIT_BLENDSHAPES))
        self.assertEqual(expressions[ARKIT_BLENDSHAPES[0]], -0.5)

    def test_ifacial_aliases_are_bidirectional(self):
        self.assertEqual(canonical_ifacial_name("eyeBlink_L"), "eyeBlinkLeft")
        self.assertIn("eyeBlink_L", ifacial_expression_aliases("eyeBlinkLeft"))
        self.assertIn("BlinkLeft", ifacial_expression_aliases("Blink_L"))

    @unittest.skipUnless(HAS_MATHUTILS, "Requires Blender mathutils; covered by Blender protocol run")
    def test_ifacial_snapshot_keeps_percentages_raw_and_mirrors_head(self):
        receiver = IFacialReceiver("127.0.0.1", 49983, "127.0.0.1", 49986)
        receiver.expressions = {"jawOpen": 1.0}
        receiver.head = (5.0, 10.0, 15.0)
        motion, raw = receiver.snapshot((0.0, 0.0, 0.0), mirror=True)
        self.assertEqual(raw["jawOpen"], 1.0)
        self.assertEqual(len(raw), len(ARKIT_BLENDSHAPES))
        expected = receiver.snapshot((0.0, 20.0, 30.0), mirror=False)[0]
        self.assertLess(
            1.0
            - abs(
                motion.bones["head"].rotation.dot(
                    expected.bones["head"].rotation
                )
            ),
            1.0e-8,
        )

    @unittest.skipUnless(HAS_MATHUTILS, "Requires Blender mathutils; covered by Blender protocol run")
    def test_vmc_uses_canonical_humanoid_names(self):
        receiver = VMCReceiver("127.0.0.1", 39539)
        receiver.bones["LeftUpperArm"] = VmcTransform(
            "LeftUpperArm",
            (0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0, 1.0),
        )
        motion, _expressions = receiver.snapshot()
        self.assertIn("leftUpperArm", motion.bones)
        self.assertNotIn("LeftUpperArm", motion.bones)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
