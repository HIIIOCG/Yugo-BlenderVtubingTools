"""Validate the direct empty-layer Add behavior and first source color."""

import sys

import bpy


WORKSPACE = r"D:\Projects\BCON26\motion_codedev"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import yugo
from yugo.panels import YUGO_MT_source_type
from yugo.properties import SOURCE_TYPES, SOURCE_UNAVAILABLE_TYPES
from yugo.state import ensure_runtime_id


def main():
    yugo.register()
    try:
        scene = bpy.context.scene
        avatar = scene.yugo_avatars.add()
        ensure_runtime_id(avatar)
        scene.yugo_avatar_index = 0

        assert bpy.ops.yugo.layer_add() == {"FINISHED"}
        assert len(avatar.motion_layers) == 1
        layer = avatar.motion_layers[0]
        assert layer.source_type == "NONE"
        assert layer.name == "MotionLayer 01"
        assert layer.color_tag == "GRAY"
        assert not layer.source_initialized
        assert not layer.spring_pause_during_playback

        layer.source_type = "VMC"
        assert layer.color_tag == "ORANGE"
        assert layer.name == "VMC Receiver 01"
        assert layer.source_initialized

        # Automatic source colors only apply to the first choice from gray.
        layer.source_type = "IFACIALMOCAP"
        assert layer.color_tag == "ORANGE"
        assert layer.name == "VMC Receiver 01"

        assert bpy.ops.yugo.layer_add() == {"FINISHED"}
        second_layer = avatar.motion_layers[1]
        assert second_layer.name == "MotionLayer 02"
        second_layer.source_type = "IFACIALMOCAP"
        assert second_layer.name == "iFacial Mocap 02"
        assert second_layer.color_tag == "GREEN"
        assert second_layer.port == "49983"

        items = layer.bl_rna.properties["source_type"].enum_items
        implemented_names = [item.name for item in items if item.identifier]
        assert implemented_names == [
            "None",
            "VMC Receiver",
            "iFacial Mocap",
            "Keybinds",
            "VRM Physics",
            "Blender Action",
        ]
        assert all(item is not None and item[0] for item in SOURCE_TYPES)
        disabled_names = [item[0] for item in SOURCE_UNAVAILABLE_TYPES]
        assert disabled_names == [
            "Rokoko",
            "Leap Motion",
            "MeddiaPipe",
            "SteamVR",
            "Mocopi",
        ]

        class FakeLayout:
            def __init__(self, records, enabled=True):
                self.records = records
                self.enabled = enabled

            def operator(self, _operator, text, icon):
                self.records.append((text, self.enabled, icon))
                return type("Properties", (), {})()

            def row(self):
                return FakeLayout(self.records, self.enabled)

            def label(self, text, icon):
                self.records.append((text, self.enabled, icon))

        records = []
        fake_menu = type("Menu", (), {"layout": FakeLayout(records)})()
        YUGO_MT_source_type.draw(fake_menu, bpy.context)
        assert [record[0] for record in records] == implemented_names + disabled_names
        assert all(record[1] for record in records[: len(implemented_names)])
        assert not any(record[1] for record in records[len(implemented_names) :])
        print("YUGO_EMPTY_LAYER_DEFAULT_OK")
    finally:
        yugo.unregister()


if __name__ == "__main__":
    main()
