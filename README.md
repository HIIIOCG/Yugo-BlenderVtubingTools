# Yugo / Yugu Node 開發中心

這裡管理 Studio 程式、開發任務與交付。Yugu Node Studio 已導入 GitHub；每個開發對話使用自己的工作副本，成果檢查後統一合併，再從 Git 中的 Studio 來源建置安裝包。

## 檔案分類

```text
addons/
  yugo/                 舊版 0.1.26 程式、資產與安裝說明
  yugu_node_studio/      Studio 程式、資產與安裝說明
tests/
  yugo/                 舊版功能測試
  yugu_node_studio/      Studio 功能、生命週期與安裝包測試
  governance/           工作副本與規範工具測試
docs/                   開工、素材、基準與架構說明
scripts/                建置、版本與開發工具
.github/                任務範本與自動檢查
```

新功能以 [Node Studio](addons/yugu_node_studio/README.md) 為主線。
[來源導入 #2](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2) 已透過
[PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15) 合併 qualified 0.6.0 基準，
包含可安裝的 Studio 建置、功能測試與 GitHub CI。[PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18)
已合併 #3 的 [公開功能 API](docs/architecture/PUBLIC_API.md)。#16 的 0.7.0 release candidate
將 Floating UI、Prop Physics 與 #14 的原生 Ragdoll Motion 接入同一個 Studio 圖、
選單、cards 與排程。Ragdoll 使用 typed Pose Source → Ragdoll Motion → Apply Ragdoll；
Start／Enter 明確啟動，恢復真正完成後才結束等待，Stop 釋放整個骨架的控制權。
最終候選驗證及正式發布另行記錄；[基準紀錄](docs/development/NODE_BASELINE.md)列出通過與受限項目。

## 從這裡開始

- [開對話的順序與可直接貼上的指令](docs/development/CHAT_STARTERS.md)
- [GitHub 任務與進度](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)
- [準備動作檔與 Blender 測試素材](docs/development/TEST_MATERIALS.md)
- [已交付成果與合併紀錄](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pulls)

開發對話自行取得 GitHub 最新主線、建立自己的 worktree，再於該工作區修改與測試。原 `motion_codedev/yugu_node_studio` 資料夾保留為唯讀參考。你只在想查看程式時，才需要在 GitHub Desktop 按 Pull origin；本機素材不會因此自動上傳。

## 開發與下載

開發者先讀 [AGENTS.md](AGENTS.md) 與 [開工及驗證流程](CONTRIBUTING.md)。架構與遷移見 [Node 系統邊界](docs/architecture/NODE_SYSTEM.md)，導入證據見 [Node 基準盤點](docs/development/NODE_BASELINE.md)。

Studio 建置入口是 `python -B scripts/node_build.py --blender "<Blender 執行檔絕對路徑>"`，會產生 `dist/yugu_node_studio-<version>.zip`。完整測試與版本檢查指令在 [CONTRIBUTING.md](CONTRIBUTING.md)，測試包與正式發布步驟在 [Studio release 流程](docs/development/STUDIO_RELEASE.md)。安裝時在 Blender 5.1+ 選擇 Install from Disk，載入該 ZIP。0.7.0 候選的 F3 功能範例與限制見 [Studio 安裝說明](addons/yugu_node_studio/README.md)；已發布版本以 [GitHub Releases](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases) 的 Studio 記錄及其來源 commit 為準。

舊版的 [功能與安裝說明](addons/yugo/README.md)及 [0.1.26 下載](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.1.26)繼續保留。本專案採 [GPL-3.0-or-later](LICENSE)。
