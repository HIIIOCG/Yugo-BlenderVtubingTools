# Yugo - Blender Vtubing

**Turn Blender into a data mixing hub for VTubing and interactive experiences.**

Yugo - Blender Vtubing is a Blender add-on for combining live tracking and interactive inputs, such as keyboard events and stream chatroom messages, in one visual workflow. Mix data from different sources to bring a character to life, then use the same building blocks to explore new ways for characters and objects to interact. Keyboard and stream chatroom support is currently in development.

![Character showcase rendered in Blender](docs/images/yugo-character-preview.webp)

## What you can do

- **Bring in live performance.** Receive body motion and expressions through VMC, and facial tracking through iFacialMocap. More tracking methods are in development.
- **Shape motion and expressions.** Blend sources, choose which parts of the body or face they affect, and adjust strength, smoothing, mirroring, and motion delay.
- **Add physical interaction.** Use Blender's rigid body settings to define how scene objects behave, and combine them with VRM spring physics for character and object interactions.
- **Build your own combinations.** Combine multiple motion sources with values, math, timing, and conditions to create your own performance and interaction setups.

## A hub for creative interaction

The idea is simple: bring data into Blender, mix and transform it, then decide how it affects your scene.

Yugo is designed to grow beyond avatar control into a toolkit for live performances, playful interactions, and experiments built inside Blender.

![Example Yugo scene inside Blender](docs/images/yugo-blender-scene.webp)

*A scene featured in our BCON26 presentation.*

## Get started

Yugo 0.2.2 includes Yugo Node and the original Yugo tools in one extension.
Verified with **Blender 5.1 and 5.2**.

Download [yugo-0.2.2.zip](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.2), install it in **Preferences → Get Extensions → Install from Disk**, and enable **Yugo**.

In the 3D View, press **N**, open the **Yugo** tab, and use **Open Yugo Node Editor** at the top. **Help** opens the handbook. Yugo Node is already included; no separate Node installation is needed.

If you previously installed a separate **Yugo Node** or **Yugu Node Studio**, turn off that older entry in **Preferences → Add-ons** before enabling Yugo. New users can skip this step.

The optional [ProtoMotions / PhysX Tracker 0.4.0](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/download/v0.2.2/yugo_physx_tracker-0.4.0.zip) replaces the standalone Physics Lab. Install its separate ZIP (about 242 MB) alongside Yugo.

- [User documentation and Getting Started](https://hiiiodigital.com/Yugo-Docs/)
- [Report an issue](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)

## License and acknowledgments

Yugo's original source code is open source under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. You may use, modify, and redistribute it, including commercially, under the GPL terms. Distributed modified versions must retain the GPL license and make the corresponding source code available to recipients. See [LICENSE](LICENSE) for the full terms.

Yugo builds on these open-source projects:

| Project | Use in Yugo | License |
| --- | --- | --- |
| [VRM Add-on for Blender](https://github.com/saturday06/VRM-Addon-for-Blender/blob/main/LICENSE_MAIN.txt) | VRM character data and integration | MIT OR GPL-3.0-or-later |
| [MuJoCo](https://github.com/google-deepmind/mujoco/blob/3.3.7/LICENSE) | Physics simulation | Apache-2.0 |
