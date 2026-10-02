# ShapeKeysValues to Objects 檢查（2026-10-01）

0.4.2 移除 Apply 與 Data → Objects 的 Collection 選項；原本存檔的 Collection 成員會一次轉成物件清單，不再動態跟隨 Collection。

Blender 5.2 背景重現測試 `blender_shapes_diagnostic.py`：

- 正常路徑通過：expression happy = 0.75、bind weight = 0.8，實際 Smile = 0.6，evaluated mesh 頂點也移動 0.6。轉換、觸發和原生套用並非全面失效。
- 空白 Add Object 列：`bool(node.objects)` 為真，視為明確指定目標；但沒有任何實際物件，因而套用零個值，只顯示 Applied 0 values。空白列不等於完全沒有列。刪除空白列或選好物件可以避開。
- 跨物件同名匹配：對清單中沒有直接 mapping 的物件，會搜尋其他物件來源的同名 Shape Key 並套用。這是原本支援跨模型套用的 fallback，但接 VRM Data 的完整 Objects 清單時，可能造成非預期的連帶套用。

上述兩项目前保留原行為，診斷測試刻意驗證其可重現性，不是理想行為的回歸規格。還沒有使用者實際 .blend，因此不能斷言哪一項就是使用者此次遇到的原因。

建議後續變更（需確認）：

1. 空白列忽略；若沒有有效手動目標且 Objects 未接線，沿用 mapping 目標。若 Objects 已接線但輸出空清單，仍保持不套用，避免誤操作別的角色。
2. 預設依 mapping 的物件＋Shape Key 名稱精確匹配；把跨模型同名套用改為可選模式，避免隱性擴散。
3. 狀態顯示有效目標数、匹配鍵數，以及物件沒有 Shape Keys / 名稱不匹配等原因。

0.4.2 僅實作 Collection 移除與相容轉換，沒有悄悄改動上述套用策略。
