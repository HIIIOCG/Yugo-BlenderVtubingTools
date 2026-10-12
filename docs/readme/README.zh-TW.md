<p align="center">
  <a href="../../README.md">English</a> ·
  <strong>繁體中文</strong> ·
  <a href="README.ja.md">日本語（建置中）</a> ·
  <a href="README.ko.md">한국어（建置中）</a><br>
  <a href="README.es.md">Español（建置中）</a> ·
  <a href="README.pt-BR.md">Português (Brasil)（建置中）</a> ·
  <a href="README.fr.md">Français（建置中）</a>
</p>

<h1 align="center">Yugo - Blender Vtubing</h1>

<p align="center">
  <strong>讓 Blender 成為 VTubing 與互動體驗的資料混合中心。</strong>
</p>

<p align="center">
  <a href="https://discord.gg/w8QMZjVPtt"><strong>Discord</strong></a> ·
  <a href="https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases"><strong>Release 下載</strong></a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/"><strong>Yugo DOC</strong></a>
</p>

<p align="center">
  <a href="https://hiiiodigital.com/Yugo-Docs/tutorials/getting-started.html">快速開始</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/index.html#what-can-you-do">你可以做到什麼</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">Showcase 展示（建置中）</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">FAQ 常見問題（建置中）</a> ·
  <a href="https://hiiiodigital.com/Yugo-Docs/">完整文件</a>
</p>

Yugo DOC 集中收錄教學、節點參考與各種專案資料，目前以英文為主。Showcase 與 FAQ 專頁建置中，這兩個入口暫時連到 Yugo DOC 首頁。

Yugo - Blender Vtubing 是一套 Blender 外掛，讓你在同一個視覺化工作流程中組合即時追蹤與互動輸入，例如鍵盤事件和直播聊天室訊息。混合不同來源的資料，讓角色動起來，再用相同的基礎工具探索角色與物件之間的新互動方式。鍵盤與直播聊天室支援目前仍在開發中。

![在 Blender 中渲染的角色展示](../images/yugo-character-preview.webp)

## 你可以做到什麼

- **匯入即時表演。** 透過 VMC 接收身體動作與表情，或使用 iFacialMocap 接收臉部追蹤。更多追蹤方式正在開發中。
- **調整動作與表情。** 混合不同來源，選擇它們影響的身體或臉部區域，並調整強度、平滑、鏡像與動作延遲。
- **加入物理互動。** 用 Blender 的剛體設定定義場景物件的行為，再結合 VRM 彈簧物理，建立角色與物件之間的互動。
- **建立自己的組合。** 將多個動作來源與數值、運算、時間和條件組合，打造自己的表演與互動設定。

## 創意互動的中心

概念很簡單：將資料帶入 Blender，混合與轉換它們，再決定資料如何影響場景。

Yugo 的目標是從角色控制延伸為一套工具，讓你在 Blender 裡創作即時表演、趣味互動與各種實驗。

![Blender 中的 Yugo 場景範例](../images/yugo-blender-scene.webp)

*這個場景曾出現在我們的 BCON26 簡報中。*

## 開始使用

**Yugo 0.2.3** 將 Yugo Node 與原本的 Yugo 工具整合在同一套擴充套件中，支援 **Windows x64**、**Linux x64** 與 **Apple Silicon Mac**，搭配 **Blender 5.1 或 5.2** 使用。Intel Mac 與 Blender 4.5 仍需要另外的相容性更新。

從 [Yugo 0.2.3 Release](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.2.3) 下載 **yugo-0.2.3.zip**。
在 Blender 開啟 **Edit → Preferences → Get Extensions → Install from Disk**，選擇 ZIP 並啟用 **Yugo**。

在 3D 視窗按 **N**，開啟 **Yugo** 分頁，再點選最上方的 **Open Yugo Node Editor**。**Help** 會開啟使用手冊。Yugo Node 已包含在套件中，無須另外安裝。

如果你正在從舊版 **Yugo 0.1.x** 更新，請先儲存場景、停用舊的 Yugo 項目，並將新的 ZIP 安裝到同一個擴充套件儲存庫。重新啟動 Blender，確認已啟用的 **Yugo** 顯示版本 **0.2.3**。如果之前另外安裝過 **Yugo Node** 或 **Yugu Node Studio**，請先停用舊項目，再啟用 Yugo。新使用者可以跳過這些更新步驟。

選用的 [ProtoMotions / PhysX Tracker 0.4.0](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/download/v0.2.2/yugo_physx_tracker-0.4.0.zip) 是獨立的 **Windows x64** 外掛，適用於 Blender 5.1/5.2。既有安裝包與校驗值維持不變；本次發行不包含 Linux 與 macOS 的 PhysX 支援。啟用 Yugo 後安裝它的 ZIP（約 242 MB），再開啟節點編輯器，選擇 **Add → PhysX Physics → Create PhysX Tracker Demo**。點選 **PhysX Physics** 節點上的 **Play**，即可試用內附的骨架與步行範例。這個示範不需要你自己的 VRM。

- [Yugo DOC 快速開始](https://hiiiodigital.com/Yugo-Docs/tutorials/getting-started.html)
- [Yugo DOC 完整文件](https://hiiiodigital.com/Yugo-Docs/)
- [加入 Discord 社群](https://discord.gg/w8QMZjVPtt)
- [回報問題](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)

## 授權與致謝

Yugo 的原始程式碼採用 **GNU General Public License 第 3 版或更新版本（GPL-3.0-or-later）** 開放原始碼。你可以依 GPL 條款使用、修改與重新散布，包括商業用途。散布修改後的版本時，必須保留 GPL 授權，並向接收者提供對應的原始碼。完整條款請見 [LICENSE](../../LICENSE)。

Yugo 使用了以下開源專案：

| 專案 | 在 Yugo 中的用途 | 授權 |
| --- | --- | --- |
| [VRM Add-on for Blender](https://github.com/saturday06/VRM-Addon-for-Blender/blob/main/LICENSE_MAIN.txt) | VRM 角色資料與整合 | MIT OR GPL-3.0-or-later |
| [MuJoCo](https://github.com/google-deepmind/mujoco/blob/3.3.7/LICENSE) | 物理模擬 | Apache-2.0 |
