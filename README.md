# Yugo / Yugu Node 開發中心

這裡管理程式、開發任務與交付。每個開發對話使用自己的工作副本，成果檢查後統一合併。

## 目前狀態

| 項目 | 狀態 |
| --- | --- |
| 多對話開發規範與隔離工具 | 已建立，[共同規則](AGENTS.md)適用後續開發 |
| Yugu Node Studio 0.6.0 | 本機交付完成；[來源導入與測試整理](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2)尚待完成 |
| 舊 Yugo 0.1.26 | 根目錄仍是已發布的舊版，保留建置與相容性 |

新功能以 Node Studio 為方向；先完成來源導入，再進行核心與功能開發。

## 從這裡開始

- [開對話的順序與可直接貼上的指令](docs/development/CHAT_STARTERS.md)
- [GitHub 任務與進度](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)
- [準備動作檔與 Blender 測試素材](docs/development/TEST_MATERIALS.md)
- [已交付成果與合併紀錄](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pulls)

開發對話負責同步自己的工作空間。你只在想查看或使用最新版本時，才需要在 GitHub Desktop 按 Pull origin；本機素材不會因此自動上傳。

## 開發與下載

開發者先讀 [AGENTS.md](AGENTS.md) 與 [開工及驗證流程](CONTRIBUTING.md)。架構與遷移見 [Node 系統邊界](docs/architecture/NODE_SYSTEM.md)，導入證據見 [Node 基準盤點](docs/development/NODE_BASELINE.md)。

舊版的 [功能與安裝說明](docs/legacy/YUGO_0_1_26.md)及 [0.1.26 下載](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.1.26)繼續保留。本專案採 [GPL-3.0-or-later](LICENSE)。
