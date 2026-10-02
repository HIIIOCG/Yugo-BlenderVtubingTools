# Floating UI feature prototype

Task #10 implements a native Blender message display using a reusable Collection
template. Each incoming message copies that template, fills its Text objects,
optionally sets a local avatar image, and moves older instances along the anchor's
local Y axis. This is scene geometry/material output; OBS can capture the resulting
Blender view as part of the stream.

The feature lives in `addons/yugo_floating_ui/`. It is an isolated development
harness, with no manifest or release package and no changes to legacy Yugo or
Studio core. Studio import #2 and the module/scheduler contract #3 remain pending.
The preview nodes below demonstrate the intended source → display behavior;
they do not claim production Interaction or chat-platform integration.

## Template preparation

Prepare a Collection containing the message layout. It may contain child
Collections, multiple Text objects, meshes, icons, and materials. Keep its objects
in template-local coordinates. The template itself is never edited by a message;
the service owns its generated instances and copied message-dependent datablocks.
Static object parenting within the Collection is preserved, including when the
template is imported as an unlinked datablock. Animated/rigged templates are outside
this prototype's static layout contract.

Use object custom properties to identify the intended bindings:

| Object custom property | Role |
| --- | --- |
| `floating_ui_role = "message"` | Text object receiving message text |
| `floating_ui_role = "author"` | Text object receiving the author name |
| `floating_ui_role = "icon"` | Object receiving the optional avatar image |

If there is only one Text object, it can be used as the message fallback. With
multiple Text objects, explicitly assign roles or select the **Message Object** by
name. **Icon Object** and **Avatar Texture Node** can bind an icon object and a
top-level Image Texture node in its material. These optional strings are deliberate
bindings, so a template does not depend on a hard-coded object name. An avatar is
an optional local image path; the prototype does not download remote URLs.

The template must include one mesh icon and a material with a top-level Image
Texture node, even when a particular message has no avatar path. An omitted avatar
preserves the template's image. When a material has several image nodes, set
**Avatar Texture Node** explicitly or label the intended node `floating_ui_avatar`.

The **Template** field accepts an existing Collection datablock. Alternatively,
set **Preset .blend** and the exact **Collection** name, then click **Import
Template**. This explicitly appends the Collection as an unlinked datablock; it
does not place the original template in the scene. The imported template remains
available after generated messages are cleared.

An optional **Anchor** object supplies the output transform. **Spacing** controls
the distance between message positions, **Maximum Messages** limits the retained
queue, and **Move Duration** controls movement of older messages. A zero duration
places the queue immediately.

## Manual development registration

Use Blender 5.1 or newer in a separate task-specific test session, with task-specific
user/config/script directories. Do not install into or reload the user's active
Blender session. From that session's Python Console:

```python
import sys
sys.path.insert(0, r"D:\Projects\BCON26\motion_codedev\.workspaces\tasks\10-floating-ui\addons")
import yugo_floating_ui
yugo_floating_ui.register()
```

Change an editor to the Node Editor, choose **Floating UI Preview**, open its
**Floating UI** sidebar, and click **Create Preview Graph**. The graph starts with
a **Message Source** connected to a **Floating UI Display**. Additional nodes are
available from **Add → Floating UI**.

1. Choose or import a template on the Display node and optionally choose an anchor.
2. Set **Message**, **Author**, and optional **Avatar File** on the Source node.
3. Click **Send Once**. Each linked active Display receives one snapshot of that
   event, with the same event ID. Sending does not start a timer or repeat messages.
4. Click **Step 0.25 s** to advance the current queue manually, or **Play 3 s** to
   run a finite preview. **Stop Preview** or Escape stops it early. Play starts only
   through this explicit action and removes its modal event timer when finished.
   Play requires an interactive Blender window; background tests should use Step
   or directly call `service.update(now=...)`.
5. Click **Clear Generated Messages** on the Display to empty its queue.

Step advances the same service timeline used by Send and Play. A later Play resumes
from that time and does not jump backwards. The preview uses a module-owned service
registry and a finite modal timer; it never registers `bpy.app.timers` or starts a
receiver, scheduler loop, or background thread automatically.

When finished, unregister explicitly:

```python
yugo_floating_ui.unregister()
```

## Public service and lifecycle

```python
from yugo_floating_ui.service import FloatingUIService, load_template

service = FloatingUIService(
    scene, template, anchor=None, max_messages=8, spacing=1.0,
    move_duration=0.25, text_object="", icon_object="", avatar_node="",
)
message_id = service.push({
    "text": "Hello!", "author": "Viewer", "avatar_path": "",
})
service.update()          # Caller owns the scheduling.
service.remove(message_id)
service.clear()
service.cleanup()
```

Payloads are plain snapshots: `text` is required, and `id`, `author`, and
`avatar_path` are optional. The service can receive explicit `now` values for
deterministic tests; normal operation uses a monotonic clock. `last_now` exposes the
current service timeline. `instances` contains the currently retained instances.
`load_template(filepath, collection_name)` appends an unlinked Collection.

The preview removes owned output when its Display is deleted or muted, its settings
or incoming links change, its scene is removed, a file loads, Undo/Redo starts, or
the feature unregisters. Reconfiguration creates a new service on the next explicit
Send. Active preview sessions stop during these changes. Clear and cleanup delete
only generated instances/data owned by this feature; they do not clear arbitrary
scene objects or delete source templates, anchors, or unrelated images/materials.
Post-load and post-Undo/Redo hooks also remove saved generated output carrying the
feature's ownership tag, so a restored preview cannot leave stale rows behind.
If a generated row, its data, or its output Collection is deleted manually, the
preview drops that invalid service and rebuilds the display on the next Send.

Future integration should adapt Interaction/chat events into this payload and call
the service through the shared scheduler established by #3. It should preserve the
one-event/one-submit rule, route moderation removals to `remove()`, and connect the
shared lifecycle to `cleanup()`. The development NodeTree and finite modal preview
are replaceable test tooling rather than a second Studio core.

## Validation status and limitations

Run the component tests with its own Blender profile and output directory:

```powershell
python -B tests/floating_ui/run_checks.py --blender "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" --demo --gui
```

`--demo` generates an original example preset, editable scene and PNG under
`artifacts/floating-ui-checks/demo/`. `--gui` runs real modal playback in a separate
GUI process, hidden on Windows, then closes that process. Neither command installs
the component in an existing Blender session. Omit these flags for the model and
background Blender suite alone. Logs and a result record stay under the chosen
artifact directory; generated binaries and external system fonts are not committed.

Validation commands, the exact Blender version, and test results belong in the task
PR. A passing legacy Yugo build does not verify this independent feature. Native
preview behavior, template copying, queue bounds, timing, and ownership cleanup need
the task's isolated Blender checks. No chat account, OBS scene, remote avatar fetch,
VRM integration, or Studio scheduler integration is part of this prototype.
