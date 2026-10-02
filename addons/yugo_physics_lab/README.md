# Yugo Physics Lab

Stage-two development preview for Yugu Node Studio. It runs prop physics while
the animation timeline stays stopped. Studio's version remains unchanged;
formal node/action integration follows repository tasks #2 and #3.

Install the separately built `yugo_physics_lab-0.1.0.zip` using Blender's
**Install from Disk**. In the 3D View press **N → Physics Lab → Create Physics Lab
Demo**, then **Start**. The preview always starts manually.

- **Apply Impulse** launches and spins the chosen prop. **Apply Force** maintains
  a force until **Clear Force**.
- **Grab Left/Right** selects the nearest free prop within Grab Range. Select and
  move its anchor using Blender's transform tools. **Release** keeps its velocity.
- **Attract Home** uses forces to return the prop to its position at Start.
  Collision stays active. **Maintain Attraction** holds it there after settling;
  **Stop Attraction** disables that force. A blocked return times out.
- **Stop** freezes the current transforms; **Reset Home** restores the Start pose.
  Changing body/collider configuration requires Stop and Start.

Dynamic props use sphere colliders, regardless of visible mesh shape. Static
meshes use oriented bounding boxes. There is an optional infinite ground plane.
This is a bounded prop prototype; articulated ragdolls and arbitrary mesh
collisions require a later backend decision.

Undo, redo, loading a file and disabling the extension clear runtime state.
The adapter stops on deleted/replaced targets, competing transform writes or
changes to captured obstacles. It never changes the animation frame or installs
anything into another Blender session.
