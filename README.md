# Yugo

Yugo is a Blender 5.1 extension for layered motion mixing on VRM avatars. It
combines live and recorded motion sources without replacing the avatar's
assigned Action or NLA data.

Current release: **0.1.14**

## Features

- Multiple VRM avatars and ordered Motion Layers
- VMC/OSC body motion over UDP
- iFacialMocap expressions and head rotation
- Non-destructive Blender Action sampling with Action Slot support
- Hold, Trigger, and Solo keyboard expression controls
- VRM 0.x and VRM 1.0 Spring Bone simulation
- Per-region humanoid and expression masks
- MToon Eevee preview optimization while preserving VRM export output
- A clean, camera-following presentation viewport

## Requirements

- Blender 5.1 or newer
- VRM avatar data created or imported by the
  [VRM Add-on for Blender](https://extensions.blender.org/add-ons/vrm/) for
  VRM-specific features
- Blender's **Allow Online Access** option enabled only when using VMC or
  iFacialMocap networking

Yugo does not download dependencies or send analytics. Its declared network
permission is used only for receiving local motion-capture data and for the
iFacialMocap start/stop commands configured by the user.

## Install

1. Download `yugo-0.1.14.zip` from the GitHub release.
2. In Blender, open **Edit > Preferences > Extensions**.
3. Open the menu in the upper-right corner and choose **Install from Disk**.
4. Select the downloaded ZIP and enable Yugo if needed.
5. Open the 3D Viewport sidebar and select the **Yugo** tab.

Do not use GitHub's automatically generated “Source code” ZIP as the install
package. Use the release asset built by Blender instead.

## Build and validate

The repository root is also the Blender extension root. With Blender available
on `PATH`:

```text
blender --command extension validate
blender --command extension build --output-dir dist
blender --command extension validate dist/yugo-0.1.14.zip
```

On Windows, `scripts/build.ps1` locates a standard Blender 5.1 installation,
checks that all version fields agree, builds the ZIP, and validates the result.

```powershell
./scripts/build.ps1
```

GitHub Actions repeats the same checks for every pull request. Pushing a tag
matching the manifest version, such as `v0.1.14`, creates a GitHub release and
attaches the validated extension ZIP.

## Development

Run the metadata check without launching Blender:

```text
python scripts/check_version.py
```

The `tests` directory contains Blender integration checks. The official package
build excludes development files, tests, caches, and existing ZIP archives.

## License

Yugo is free software licensed under
[GPL-3.0-or-later](LICENSE).

---

## 中文快速說明

Yugo 0.1.14 是 Blender 5.1 的 VRM 動作圖層工具，可混合 VMC、
iFacialMocap、Blender Action、鍵盤表情與 Spring Bone。安裝時請下載
GitHub Release 裡的 `yugo-0.1.14.zip`，再從 Blender 的
**偏好設定 > Extensions > Install from Disk** 安裝。

只有使用 VMC 或 iFacialMocap 時才需要開啟 Blender 的
**Allow Online Access**；Yugo 不會下載相依套件，也不會傳送分析資料。
