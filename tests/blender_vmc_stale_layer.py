"""Validate that a removed Motion Layer is never written through stale RNA."""

import sys

import bpy


WORKSPACE = r"D:\Projects\BCON26\motion_codedev"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

import yugo
from yugo.state import ensure_runtime_id
from yugo.vmc import runtime


def main():
    yugo.register()
    try:
        armature_data = bpy.data.armatures.new("YugoVmcStaleLayerData")
        armature = bpy.data.objects.new("YugoVmcStaleLayer", armature_data)
        bpy.context.scene.collection.objects.link(armature)

        scene = bpy.context.scene
        avatar = scene.yugo_avatars.add()
        ensure_runtime_id(avatar)
        avatar.armature = armature
        layer = avatar.motion_layers.add()
        layer.source_type = "VMC"
        layer_id = ensure_runtime_id(layer)
        target = runtime._Target(scene, layer, armature, {})

        extra = avatar.motion_layers.add()
        extra.source_type = "BASE_ACTION"
        assert target.resolve_live()
        assert target.layer.runtime_id == layer_id
        avatar.motion_layers.remove(1)
        assert target.resolve_live()

        avatar.motion_layers.remove(0)
        assert not target.resolve_live()
        target.clear()

        # Reproduce the old crash route without relying on undefined Blender
        # memory: submit() fails because its RNA is stale, and clear() must
        # never be called by the timer's exception handler.
        receiver = runtime._Receiver("127.0.0.1", 0)
        original_socket = receiver.socket

        class StaleTarget:
            layer_key = "simulated-stale-layer"
            layer_pointer = 918273

            def __init__(self):
                self.clear_count = 0

            def submit(self, _root_buffer, _bone_buffer, _delta_time):
                raise RuntimeError("simulated freed Layer RNA")

            def clear(self):
                self.clear_count += 1
                raise AssertionError("The VMC timer touched stale RNA during cleanup")

        stale_target = StaleTarget()
        receiver.targets[stale_target.layer_key] = stale_target
        next_interval = receiver._timer()
        assert next_interval == runtime.APPLY_INTERVAL
        assert stale_target.clear_count == 0
        assert receiver.targets[stale_target.layer_key] is stale_target
        assert receiver.socket is original_socket
        assert receiver.socket.fileno() >= 0
        receiver.targets.clear()
        receiver.close()
        print("YUGO_VMC_STALE_LAYER_SAFE_OK")
    finally:
        yugo.unregister()


if __name__ == "__main__":
    main()
