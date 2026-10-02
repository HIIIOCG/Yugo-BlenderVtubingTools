# Yugu Node Studio

**尚未匯入程式，目前不可建置或安裝。** 這個目錄是後續 Node 主線的說明入口。
結構整理 [#8](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/8)
合併後，由[任務 #2](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2)
匯入來源並建立可重現的測試基準。

來源與已知驗證缺口見[基準盤點](../../docs/development/NODE_BASELINE.md)。
測試將放 `tests/yugu_node_studio/`；目前該目錄尚未建立。

## 功能責任分類

| 分類 | 責任 |
| --- | --- |
| 核心與資料 | Node／socket 身分、資料契約、排程與生命週期 |
| 動作來源與處理 | VMC、iFacialMocap、Action、組合與平滑 |
| 角色輸出與物理 | VRM 快照、Pose／Shape Key、Spring 與控制權 |
| 鍵盤與動作流程 | 輸入狀態、模式、等待、完成及取消 |
| 編輯介面 | 節點、卡片與圖形編輯 |
| 攝影機與影像 | 攝影機、合成與材質功能的接入 |

這是規劃中的責任分類，不表示已有這些子目錄或完整 registry。#2 先保留來源
行為完成導入，#3 再建立公開介面與功能子分類；#4 可在 #2 合格後並行驗證。
詳細契約只維護在[Node 系統邊界](../../docs/architecture/NODE_SYSTEM.md)。
