# Yugu Node 系統邊界與遷移方向

新開發以 Yugu Node Studio 為主產品方向，GitHub 為來源與交付管理中心。
這份文件定義後續的責任邊界與遷移規則，並區分 repository 已有內容和
待實作架構，避免不同聊天把計畫中的功能當成既有依賴。

## 現況與目標

| 項目 | 目前狀態 | 後續規則 |
| --- | --- | --- |
| `addons/yugo/` | repository 現有舊產品；manifest ID 為 `yugo`，版本為 0.1.26 | 保留 package ID、既有發布與必要修正 |
| `addons/yugu_node_studio/` | #2 已透過 PR #15 導入 qualified 0.6.0 基準，含 runtime、資產、建置及 CI | 新開發以 Git 工作區的 Studio 來源為準 |
| `yugu_node` | 外部已有另一份 Node 實作 | 當作比較／遷移來源；不建立第二條持續獨立演進的核心 |
| 任務工作區與範圍檢查 | 本次治理工具提供 worktree 啟動及修改範圍檢查 | 每個實作聊天使用自己的工作區 |
| 模組 registry／自動載入契約 | #3 核心介面工作進行中；以該 PR 的驗證及合併紀錄為準 | 消費端在介面 qualified 整合後接入 |
| 中央資源鎖／lease service | 尚未實作 | 先由整合角色登記與排序；有需要再加後端 |

Studio 的來源版本、封裝比較及測試結果以 [Node 基準紀錄](../development/NODE_BASELINE.md)
及各次整合 PR 為準。#2 已通過來源、ZIP 及 portable regressions；正式 release
另記錄實際發布的 tag、來源 commit、安裝包 checksum 與必要人工驗收。

程式放 `addons/`，功能測試分在 `tests/yugo/` 與
`tests/yugu_node_studio/`，工作區工具測試在 `tests/governance/`。外掛必需資產
保留在各自套件；大測試素材與其交接依[素材指南](../development/TEST_MATERIALS.md)，
不另建共用素材資料庫。任務 #3 在這個合併基準上落實功能子分類與公開介面；
規劃中的子目錄仍須以實際來源與通過的驗證為依據。

## 程式責任要怎麼切

下表是建議的模組責任。Studio 已導入同名或相近檔案；各領域的模組化與
公開契約仍須分任務逐步落實，並保留合併基準的行為。

| 領域 | 責任與邊界 | 主要需要協調的介面 |
| --- | --- | --- |
| Core data／node contracts | 共用資料型別、socket 語意、node ID、儲存 schema | `data`、`constants`、node/socket 定義及遷移 |
| Scheduler／lifecycle | 共用更新順序、啟停、Undo／Redo、Load、快取清除 | `scheduler`、註冊入口與 runtime 清理 |
| Motion sources | VMC、iFacialMocap、Blender Action 等輸入與解碼 | `network`、`protocols`、`action_source` 的輸出契約 |
| Motion processing | 標準化 Humanoid 資料的組合、平滑、權重及遮罩 | `processing`、map／value 資料與輸入輸出格式 |
| Character output／VRM snapshot | 角色映射快照、native Blender Pose Bone／Shape Key 寫入 | `vrm_cache`、`runtime`、目標資源與還原狀態 |
| Spring physics | 物理狀態、求解與輸出時序 | `spring_solver`、角色輸出及 scheduler 契約 |
| Keyboard／control state | 鍵盤來源、狀態、控制模式與事件 | `keyboard`、`input_nodes`、`input_runtime` |
| Action flow | 觸發、等待、完成、取消、錯誤及可重用流程 | `flow`、`workflow`、對外動作介面 |
| Editor／cards UI | 展示、編輯及操作現有資料／圖 | `ui`、`studio`、`workflow_ui` 和資料持久化 |
| Camera／composing／materials | 影像、攝影機、合成與材質功能 | 先用任務界定既有來源及與 scheduler 的連接方式 |

每個功能任務選擇其中一個主要領域，allowed paths 包含實際要改的檔案。
責任領域方便分工，不能代替修改路徑檢查，也不能讓功能任務自行改共用契約。

## 共用契約的規則

- **Stable identities**：既有 node ID、socket type、持久化屬性與 package ID
  是相容性介面。改名、移除或改語意時，必須說明舊 `.blend` 圖如何讀取或遷移。
- **Data versus runtime**：需要持久化的圖／參數與暫存連線、計時器、執行狀態
  分開處理。Load、Undo／Redo 及重載時不得留下指向已釋放 Blender RNA 的狀態。
- **One scheduler contract**：新模組接入共用執行順序；新增獨立 timer、handler
  或 receiver thread 屬於需協調的核心改動。介面須描述註冊、停止與清除方式。
- **Output ownership**：寫入角色、材質、攝影機或其他 Blender 資料時，明確定義
  誰擁有該 target、何時生效與何時還原。多個來源或動作寫同一 target 必須有
  明確的優先序／組合規則。
- **Completion and cancellation**：動作要定義開始、完成、錯誤及取消的語意，
  並能在斷線、刪除、重接與重載時釋放資源；UI 不另外建立隱藏的執行狀態。
- **Dependencies**：跨模組依賴由 Issue／PR 記錄，core 變更先進入整合，消費端
  再以合併後的基準開發或更新。相同檔案沒有 Git conflict 仍須驗證執行結果。

未來若新增 registry，其第一個任務應定義 module ID、依賴、註冊／解除、
node／socket 宣告及 lifecycle 行為，再選一個現有模組試接。不要先讓多個
聊天各寫一套 registry 或宣稱所有模組已可隨插隨用。

## 核心改動如何接入

### 第一個公開擴充介面必須處理的項目

- Value 是可讀取的當前資料；event 是一次觸發的 payload；action 的 End
  表示真正完成，並分別定義 Failed、Cancelled 與 timeout。不得以一次 tick
  當成非同步動作完成。
- Event payload 使用可 JSON 序列化的快照；跨等待不得持有 Blender RNA／context。
  Motion 與 native pose 的型別另依核心資料契約，不把所有運算資料強制轉 JSON。
- 功能模組透過公開 start/update/cancel 等動作介面工作，不操作 workflow 的
  `_runs`、`_tasks`、`_Pulse` 等私有狀態。
- 擴充宣告提供節點 classes、evaluator、action handler、menu/card 描述及
  register/unregister/cleanup；統一透過核心 registry 接入，避免每個功能修改
  相同分派檔案。完整 registry 尚待任務 #3 實作。
- 現行 target 仲裁主要涵蓋 bone rotation／shape key；object transform、camera、
  physics、material 的控制權需擴充公開協議，不能假設現有核心已完整支援。

共享 schema、node/socket、scheduler、lifecycle、registration、資料持久化及
release 路由的改動由 `core` 或 `integration` 任務處理。PR 必須列出：

1. 改變的契約及受影響消費端。
2. 舊圖／舊設定的相容方式，或明確遷移步驟。
3. 需先合併或後跟進的任務。
4. 最新整合候選的測試、版本、依賴與已知限制。

整合角色對這些修改排序，對最新 `main` 的實際候選做驗證。各聊天的工作區
互不覆寫，只解決檔案層問題；核心互動仍靠上述契約與整合順序處理。

## Node 基準與後續整合的交付條件

結構整理 #8 之後，任務 #2 已由 [PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15)
建立合格基準，後續核心、測試及功能任務從合併主線各自開工。基準與候選整合
仍遵循以下交付條件：

1. 固定 Git 來源 commit 與採用的依賴，記錄版本和檔案差異；一次性外部來源
   留在基準紀錄，後續不再把共享資料夾變更直接同步進 `main`。
2. 來源匯入 `addons/yugu_node_studio/`，測試放 `tests/yugu_node_studio/`；核對
   package ID、資產／授權及 fixture，保留 `addons/yugo/` 與 release 歷史。
3. 讓 Node 測試明確載入該 Node package；跨套件 fixture／VRM Add-on 依賴
   必須可取得並記錄。失敗或缺 prerequisite 的測試不得列為通過。
4. 執行 Node 專屬版本檢查、extension validate／build、安裝及功能測試。
   `addons/yugo/` 的版本與打包流程繼續可用。
5. 決定多產品的版本／tag 路由，驗證 Studio tag 不會走 legacy `v*` workflow
   產生錯誤安裝包；發布由 integration 角色依授權執行。
6. 合併並完成驗證後，把後續 Node 任務切到 Git 工作區。外部來源留作參考
   快照，停止同時編輯兩套正式程式或做自動雙向同步。

Legacy → Node 的行為遷移採後續獨立 PR，不因為新系統成為主線就刪除舊版本
或把現有 `yugo` package ID 改成 Studio。這樣既有使用者能維持可回退的版本，
新功能也能在單一主線依序整合。
