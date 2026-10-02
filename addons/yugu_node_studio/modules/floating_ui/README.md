# Floating UI in Studio

This module adapts the native Blender display from merged PR #12 into Studio's
public feature API. `model.py` and `service.py` are byte-identical to the tracked
`addons/yugo_floating_ui/` implementation. The standalone preview's node tree,
timer, modal operator, and handlers are not imported.

Use F3 **Create Studio Floating UI Example** to create an editable template,
anchor, and a normal Studio graph. The **Message Source** node's **Send Once**
button emits one event with a fresh ID. Its `Message` output connects to the
**Floating UI Display** node's `Enter` input. Enter message text, author, and an
optional local avatar image file. The example uses generated geometry, text, and
a generated placeholder avatar; it has no external asset requirement.

An event payload is a JSON object:

```json
{"id":"unique-message-id","text":"Hello!","author":"Viewer","avatar_path":""}
```

`text` is required. `id`, `author`, and `avatar_path` are optional; missing IDs
receive a UUID. An active duplicate ID preserves the existing immutable row.
Only local image files are supported. Platform receivers and OBS integration
remain separate work.

Choose an editable template Collection containing one FONT message object and
one MESH avatar object with an Image Texture node in its material. An optional
FONT author is supported. Mark objects with the `floating_ui_role` custom
property (`message`, `icon`, `author`), or set the explicit object/texture node
bindings. Existing parenting, static layout, nested collections, geometry, and
material settings are copied into each row. Templates can remain unlinked from
the scene. **Import Template** explicitly appends the named Collection from the
selected `.blend` preset. Existing template and anchor objects are retained.

Rows stack along the anchor's local Y axis. Maximum Messages bounds the live
queue; Spacing and Move Duration control smooth movement. An arrival during
movement continues from the current position. `End` means the row was inserted;
the shared Studio scheduler continues animating afterwards. `Failed` reports an
invalid payload, layout, or avatar. A failed new row preserves a valid existing
queue. `Cancel` affects a pending action invocation; completed display rows are
cleared by the `Clear` event input or **Clear Generated Messages** button.

The Display is also available in Studio workflow cards; cards edit the same
node. Configuration changes, direct or upstream rewiring, mute, node removal,
or generated object removal retire the old display and release its transform
claims. Send another message to create a display for the new configuration.
Claims cover only generated display objects, never the template or anchor.

Load, Undo/Redo, and reload retire runtime state. Recovery deletes only generated
data carrying both the original service owner and the additional Studio tag,
`yugus_floating_ui_generated`. Standalone PR #12 preview outputs carry only the
original owner tag and are preserved. Restricted registration/unregistration
does not read scene data; first-enable recovery waits for the shared scheduler
or first action. Adopted geometry/materials/images with other users are retained.

Public example/test helpers are `create_example(context)`,
`make_example_template(name)`, `send_message(source, scene, now=None)`, and
`get_service(display, scene)`. Tests are `test_floating_ui_model.py` and
`blender_floating_ui.py`; the Blender test covers actual event routing, cards,
claims, queue animation, topology changes, preset append, actual Undo/Redo,
file load, and restricted reload.
