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

Yugo Node requires **Blender 5.1 or newer**.

Install a Yugo Node ZIP in **Preferences → Get Extensions → Install from Disk**, enable the add-on, and restart Blender.

- [User documentation](https://github.com/HIIIOCG/Yugo-Docs)
- [Report an issue](https://github.com/HIIIOCG/Yugo-BlenderVtubing-Dev/issues)

## License and acknowledgments

Yugo's original source code is open source under the **GNU General Public License v3.0 or later (GPL-3.0-or-later)**. You may use, modify, and redistribute it, including commercially, under the GPL terms. Distributed modified versions must retain the GPL license and make the corresponding source code available to recipients. See [LICENSE](LICENSE) for the full terms.

Yugo builds on these open-source projects:

| Project | Use in Yugo | License |
| --- | --- | --- |
| [VRM Add-on for Blender](https://github.com/saturday06/VRM-Addon-for-Blender/blob/main/LICENSE_MAIN.txt) | VRM character data and integration | MIT OR GPL-3.0-or-later |
| [MuJoCo](https://github.com/google-deepmind/mujoco/blob/3.3.7/LICENSE) | Physics simulation | Apache-2.0 |
