# Yugo / Yugu Node 開發中心

這裡管理程式、開發任務與交付。每個開發對話使用自己的工作副本，成果檢查後統一合併。

## 檔案分類

```text
addons/
  yugo/                 舊版 0.1.26 程式、資產與安裝說明
  yugu_node_studio/      新主線入口，目前只有說明，尚未匯入程式
tests/
  yugo/                 舊版功能測試
  governance/           工作副本與規範工具測試
docs/                   開工、素材、基準與架構說明
scripts/                建置、版本與開發工具
.github/                任務範本與自動檢查
```

新功能以 [Node Studio](addons/yugu_node_studio/README.md) 為方向；
[結構整理 #8](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/8)合併後，
先完成[來源導入 #2](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2)，
再並行進行核心與測試。Node 測試目錄將在導入時建立；目前沒有可安裝的 Node 套件。

## 從這裡開始

- [開對話的順序與可直接貼上的指令](docs/development/CHAT_STARTERS.md)
- [GitHub 任務與進度](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)
- [準備動作檔與 Blender 測試素材](docs/development/TEST_MATERIALS.md)
- [已交付成果與合併紀錄](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pulls)

開發對話負責同步自己的工作空間。你只在想查看或使用最新版本時，才需要在 GitHub Desktop 按 Pull origin；本機素材不會因此自動上傳。

## 開發與下載

開發者先讀 [AGENTS.md](AGENTS.md) 與 [開工及驗證流程](CONTRIBUTING.md)。架構與遷移見 [Node 系統邊界](docs/architecture/NODE_SYSTEM.md)，導入證據見 [Node 基準盤點](docs/development/NODE_BASELINE.md)。

舊版的 [功能與安裝說明](addons/yugo/README.md)及 [0.1.26 下載](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.1.26)繼續保留。本專案採 [GPL-3.0-or-later](LICENSE)。
