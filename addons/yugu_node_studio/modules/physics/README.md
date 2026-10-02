# Studio prop physics

In Blender press F3 → **Create Studio Physics Example**, then open the generated
**Studio Physics Example** node graph. Its controls provide Start, Stop, Reset,
Impulse, Grab, Release, Attract, Stop Attraction, Force and Clear Force. The world
starts only on Start; a saved scene never resumes simulation automatically.

Dynamic props use sphere colliders, static mesh obstacles use oriented bounding
boxes, and one world supports up to 32 props. Configure independent writable
objects without parents, constraints, animation, drivers or native rigid bodies.
Pause the animation timeline before starting. Start captures the current home;
Stop freezes the current pose; Reset restores captured home and clears motion.

Grab attaches the nearest free prop to the selected configured anchor. Move the
anchor with Blender tools and Release to preserve linear and angular velocity.
Impulse applies momentum once. Force remains until Clear Force, Reset or restart.
Their End means the command took effect. Attract Home remains Running until the
prop stays within position/orientation and speed tolerances for Stable Duration;
a blocked return emits Failed on timeout. Cancel stops only that attraction.
Maintain Attraction keeps the force after End; Stop Attraction removes it.

Competing active attractions fail. Restart cancels the earlier invocation before
starting its successor. Grab or Stop Attraction cancels the affected pending
attraction without cancelling another prop. World mutation, deletion, scene
replacement, writer conflict and lifecycle changes retire the world and release
its transform claims. User props are never deleted by lifecycle cleanup.

This module uses Studio's public registration/actions/ownership API and shared
motion scheduler. It adds no independent timer or Blender lifecycle handler.
`solver.py` and `service.py` are byte-identical copies of the completed PR13
prototype sources; its standalone preview is excluded from Studio.
