# Yugu Node Studio 基準盤點

盤點日期：2026-10-02（Asia/Taipei）。狀態：**本機候選已交付；repository 導入與 qualification 待任務 #2 完成**。

## 來源與產品身分

- 來源聊天：修复 Yugo Node Studio 弹跳；盤點時為 idle，最新工作已 completed。
- 本機來源：D:/Projects/BCON26/motion_codedev/yugu_node_studio。
- 新產品 addon ID：yugu_node_studio；候選版本：0.6.0。
- yugu_node（0.8.0）是另一個獨立 extension，並非 Studio 的 runtime 依賴。不要同步回補兩套 Node 核心。
- 舊 Yugo 0.1.26 在 `addons/yugo/`；`addons/yugu_node_studio/` 目前僅有說明，尚未匯入 Studio 程式。
- 正式匯入須等結構整理 #8 合併，再由任務 #2 使用最新主線；來源放 `addons/yugu_node_studio/`，測試放 `tests/yugu_node_studio/`。
- 已逐一比較候選 ZIP 的 35 個打包檔案與本機來源，盤點時完全一致。未提交生成 ZIP 或 .blend 範例。

候選安裝包 SHA-256：

```text
53b6dad3873bdb973b806165837c90c1bae02f50a2c093524b1bea2bb0635379
```

候選範例 .blend SHA-256：

```text
a3535ed30285902d6ade776050e36ca1044c21e01aa50aab45a56f07a65b112a
```

導入前重新核對來源與聊天狀態，並把匯入的來源 commit 與測試結果補在本文件。本機路徑是一次性匯入來源，不是未來可寫的第二套主線。

## 已交付的 0.6.0 行為

- Windows 全域鍵盤監聽需明確啟動，按鍵仍傳遞給前景應用。
- Keyboard Data、Key State、State Logic、State Events、Toggle、State Value、Control Mode、Mode Match、Set Mode。
- tick 間保留 press/release；模式鎖定與解除後避免 held-key 重觸發。
- 可重用 Action Flow、Flow Entry/End、非同步 Wait Action/Test Action。
- Parallel、Wait All、Repeat、每次呼叫各自取消／失敗及 parallel/ignore/restart 忙碌策略。
- 卡片編輯對應實際圖形；不支援的圖形保留連線並可回到 graph 編輯。
- 等待與並行呼叫保留 event payload 快照；連線變更、刪除、mute、undo、load 清理相關工作。
- 範例提供 Start/Cancel/Edit Cards；F8 切模式、Ctrl+R 啟動、Escape 取消。

## 驗證狀態與已知缺口

以下是來源聊天的既有執行紀錄，不代表此 repository 的 Node CI 已建立或重新全套通過。

| 檢查 | 盤點結果 |
| --- | --- |
| workflow、workflow UI、stage-one、saved example、input nodes、flow controls、spring smoothing | 來源聊天有 PASS 記錄 |
| Extension build / validate、package behavior | 來源聊天有 PASS 記錄 |
| 全域實際鍵盤捕捉 | 來源聊天另有真人桌面 PASS 回報；sandbox 測試只安裝 hook 並略過實際按鍵 |
| blender_integration.py | 最後紀錄 exit 1；line 273 的舊 Character Output activation 斷言尚未適配已退役 scheduler |
| blender_package.py 可攜性 | 依賴 dist/yugo-0.1.29.zip 比對 preset.blend，乾淨 Studio checkout 無法直接重現 |

任務 #2 必須調查測試與現行 Hz Trigger/Apply 契約、移除跨舊產物依賴並建立乾淨環境驗證。不得把失敗標成通過，或為舊測試恢復已退役的執行路徑。

## 完成導入的條件

來源／資產已納入 `addons/yugu_node_studio/`，測試在 `tests/yugu_node_studio/`；必要測試可在乾淨 clone 重現；Node 與 legacy 分開打包；既有 node/socket ID 與場景相容性保留；未完成項目有 Issue。由整合角色記錄結果後把本文件狀態改成 qualified。小型受控 fixture 可放 `tests/yugu_node_studio/fixtures/`，該目錄目前尚未建立。

來源導入、測試合格與正式 Release 是分開的里程碑；本次管理空間整理不建立 Node Release。
