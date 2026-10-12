# 多聊天並行開發規範

目標是讓每個聊天在自己的工作區完成一個可交付任務，再由整合角色把成果
接回同一個 GitHub 專案。工作區隔離防止檔案互相覆寫；任務範圍、依賴順序
及整合測試負責處理介面和行為衝突。

本規範延伸既有 [CONTRIBUTING.md](../CONTRIBUTING.md)、Issue／PR 範本、
版本檢查及 Blender 打包流程。主產品的轉換另見
[Node 系統邊界](../../docs/architecture/NODE_SYSTEM.md)。

## 管理資料放在哪裡

| 資料 | 管理位置 | 誰維護 |
| --- | --- | --- |
| 任務、範圍、負責聊天、依賴、測試需求 | 一個任務一個 GitHub Issue | 任務負責者，整合角色協調認領 |
| 程式修改與驗證證據 | 一個任務一個 PR | 任務負責者 |
| 規範與架構決定 | `AGENTS.md`、`.github/ai/`、`docs/`、`.github/CONTRIBUTING.md` | 文件任務；核心決定由整合角色協調 |
| 本機工作區身分 | 每個工作區的 `.dev/task.json` | 啟動工具與該任務 |
| 合併順序、共享資源使用時間 | Issue／PR 的明確紀錄 | 整合角色 |

GitHub Issue／PR 是團隊可讀的工作紀錄；`.dev/task.json` 是本機檢查需要的
工作區資料。不要讓不同聊天各自維護一份彼此不同步的總表，也不要把瀏覽器
`localStorage` 當成跨聊天鎖定服務。

目前任務與資源登記由整合角色依序處理。自動搶占、到期租約、心跳及全域
鎖定服務屬於未來可擴充項目；Issue 留言本身不提供原子鎖定。

## 一個聊天如何開始

1. 建立或認領一個 Issue，寫清楚成果、修改範圍、角色、依賴及驗收條件。
   任務 ID 必須唯一，並且能對應回 Issue；文件中的示例 ID 並不代表已有任務。
2. 整合角色確認沒有重複認領、確定起始基準及需要等待的核心工作。
   已獲授權的整合角色可自行建立任務，不必為例行拆分反覆要求使用者批准。
3. 負責聊天自行 `git fetch origin` 並核對最新 `origin/main`，保留任何既有修改；
   再使用 `scripts/start_task.py` 建立新的 Git worktree、branch 和 `.dev/task.json`。
   把新工作區路徑提供給負責聊天，後續所有編輯與測試都使用該路徑。
4. 負責聊天閱讀根目錄 `AGENTS.md`、任務紀錄及相關模組說明，核對實際路徑、
   branch、`base_sha` 和 `allowed_paths`，才開始修改。

Windows 啟動範例，請替換任務 ID 和描述：

```powershell
python -B scripts/start_task.py --task 123 --slug camera-targets --role feature --scope "addons/yugu_node_studio/modules/camera/**" --scope "tests/yugu_node_studio/blender_camera.py" --workspace-root D:\Projects\BCON26\motion_codedev\.workspaces\tasks
```

公開功能介面已由 #3／[PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18)
完成 qualified 整合，契約見 [PUBLIC_API.md](../../docs/architecture/PUBLIC_API.md)。`modules/camera/`
仍是示意範圍，不能當作該模組已存在的依據。實際 scope 一律複製最新 Issue，
並讀取工作區的真實來源，不能用示例自行擴大範圍。

`--workspace-root` 是放置新工作區的絕對父目錄，必須在執行工具的 repository
之外。不要把新工作區放進該 repository 的原始碼或打包內容中。範例父目錄
與目前治理工作區是並列位置；實際設定以任務啟動工具檢查結果為準。

`.dev/task.json` 的資料格式為 `schema_version: 1`，包含 `task_id`、`role`、
`branch`、`workspace`、`base_sha`、`allowed_paths`、`dependencies`、`resources`
及 `status`。`protected_paths` 由範圍檢查工具的政策決定。`.dev/task.json` 是
本機身分資料，不能代替 GitHub 上的依賴、範圍及整合紀錄。

`start_task.py` 不會自動 fetch。開發中固定已記錄的基準；採用新合併依賴時，
聊天再次 fetch、明確整合並更新基準，重跑受影響檢查。整合角色在測試候選前
取得最新主線。這些操作由正在工作的聊天執行，沒有背景輪詢或閒置聊天自動
同步；也不要求老闆反覆替開發工作按 Pull origin。

## 角色與修改範圍

| 角色 | 工作內容 | 交付方式 |
| --- | --- | --- |
| `docs` | 規範、需求、說明文件 | 文件 PR |
| `feature` | 在既有契約下新增或修正一個功能 | 有範圍及驗證證據的功能 PR |
| `core` | 共用資料、節點／socket 契約、排程、生命週期或註冊 | 列出消費端及遷移方式的核心 PR |
| `integration` | 任務協調、依賴排序、來源遷移、合併及版本發布 | 整合候選、驗證與發布紀錄 |

Allowed paths 是允許修改的 repository 相對路徑或 pattern，不是建議。
範圍應包含相關測試及必要文件，並儘量精確。新增另一個功能、修改核心契約、
改版本號或編輯另一任務的程式，不能視為原任務的順手修改。

範圍必須變更時，先把理由、影響、角色與依賴更新到 Issue／PR，再同步本機
任務紀錄。不能只在本機放大 allowlist 或提高角色來繞過失敗的檢查。
核心受保護路徑以 `scripts/check_task_scope.py` 的實際政策為準；沒有被路徑
規則攔下的修改，也可能有共用介面或行為影響，負責者仍須提出。

同一模組可以在隔離工作區並行研究，甚至完成互不依賴的修改，但需事先記錄
重疊範圍。若改同一介面、資料 schema、socket 或共用執行流程，就指定前後依賴，
由整合角色依序接入。不要用「Git 沒有文字衝突」作為行為相容的證明。

## 每次交付前的檢查

```powershell
python -B scripts/check_task_scope.py
```

本機檢查使用 `.dev/task.json` 的已登記基準與範圍，不接受臨時覆寫。
CI／整合檢查使用 `--ci --base <commit> --role <role>` 及重複的
`--scope <pattern>`，並先核對 GitHub Issue 與 PR 的 canonical 宣告。

本機檢查包含相對基準的提交差異，以及 staged、unstaged 和 untracked 檔案；
同時核對工作區、branch、角色及受保護路徑。它可以發現不在範圍內的修改，
不能阻止任意工具寫入其它目錄，也不能自動判斷所有語意衝突。

GitHub Issue 與 PR 都必須有且只有一個 `task-scope` JSON code block，包含
整數 `task_id`（Issue 編號）、`role`、非空 `allowed_paths`。統籌角色先在 Issue
確認範圍，PR 複製相同宣告；CI 會讀取對應 Issue 比對，不只相信 PR 自填角色。
Issue 的負責聊天與工作路徑仍由統籌依序認領，並非帳號權限或原子鎖。
開發中的 PR 必須對應仍開啟的 Issue。已關閉 PR 的說明更新不執行開發範圍
檢查；若事件排隊期間 PR 已合併、Issue 已自動關閉，checker 會即時查詢
同一 PR，只有確認相同 head commit 已合併才明確略過失效的開發檢查。
PR 仍開啟、未合併、head 不符或無法確認時，關閉 Issue 仍會被拒絕。
CI 的範圍檢查與 extension 驗證提供可重現的關卡；main 的 branch
protection 是 repository 設定，需要另外核對，不能因為新增文件就宣稱啟用。

驗證要求依變更決定：文件任務檢查內容、連結及範圍；功能任務執行相關功能
測試；核心任務包含受影響模組、Undo／Redo、Load、啟停／重載及舊圖相容性等
適用項目。現有 legacy 版本檢查與 extension 驗證繼續保留。

## Blender 與其他共享資源

工作區隔離還不夠：不同 Blender 程序仍可能使用同一組設定、安裝路徑、UDP
port 或檔案。每個任務必須：

- 使用任務專屬 Blender user/config/script 路徑與獨立測試程序，記錄 Blender
  版本、VRM Add-on 等必要依賴及實際載入的程式路徑。
- 使用自己工作區內的輸出、log、快取及 `.blend` 複本；不要在共用範例檔上
  原地儲存，不要讓兩個任務覆寫同名 ZIP。
- 使用不重複的接收 port；硬體、固定 port 或全域鍵盤等無法隔離的測試資源，
  在任務中記錄負責者、資源名稱、開始／結束時間及後續使用者，由整合角色
  排定使用順序。不要自行解除另一個任務的占用。
- 不對使用者目前開啟的 Blender 安裝、重載或關閉外掛作為例行驗證。確實
  要驗證現場環境時，把該動作列為明確的獨立驗收步驟。

在 PowerShell 可把 `BLENDER_USER_CONFIG`、`BLENDER_USER_SCRIPTS` 及所需
Blender user 路徑設成任務目錄，再啟動獨立程序；`--factory-startup` 本身
不能保證所有檔案、擴充套件與 port 都已隔離。測試載入 repository 程式時
也要核對路徑，避免誤測已安裝的另一版本。

## 任務狀態與整合順序

建議的人工管理狀態如下；這些是 Issue／PR 的協作狀態，不代表目前存在
自動狀態機或 GitHub Project 欄位：

`待規劃 → 可認領 → 開發中 → PR 待檢查 → 排入整合 → 已合併`

需要別的任務先完成時，標記「等待依賴」，寫出 Issue／PR 和所需契約。
暫停、失敗或取消時保留修改、驗證與下一步，不刪除其他人的工作區。

整合角色處理一個可合併候選時：

1. 確認任務、allowlist、依賴與 PR 內容相符，檢查沒有混入其他人的變更。
2. 取得最新 `main`，建立實際待合併候選，在整合工作區處理衝突。
3. 對該候選執行範圍、版本、建置及受影響功能／相容性檢查；記錄 commit。
   `main` 或候選又變動時，重新執行受影響檢查。
4. 合併後更新 Issue／PR、資源占用與下一個依賴任務。沒有測過最新候選時，
   不把舊分支上的成功結果當作整合完成。

整合排序由一個角色依序執行，不讓多個聊天同時合併或替同一版本打 tag。
其角色可由已獲授權的技術主管聊天擔任，並不要求每一步都由使用者手動批准。

## 版本、發布與來源遷移

功能任務預設保留目前 product version，由整合角色安排整批成果的版本。
只有整合角色可更新 release metadata、建立正式 tag 及執行已獲授權的發布。
已發布 tag／asset 保留；需要修正時發布新版本。

`v*` tag 對應 `addons/yugo/` 的 legacy release workflow。Studio package ID 是
`yugu_node_studio`，版本檢查採 `studio-v<version>`；integration 角色在正式發布
前驗證 Studio 的 release route 與候選安裝包，不把 Studio tag 交給 legacy workflow。

結構整理 #8 與來源導入 #2 已完成；[PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15)
把 qualified Studio 0.6.0 基準導入 `addons/yugu_node_studio/`，測試放
`tests/yugu_node_studio/`。來源、檔案比對、可重現入口及受限項目記錄在
[NODE_BASELINE.md](../../docs/development/NODE_BASELINE.md)。#3 公開介面已透過
[PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18) 合併；功能消費端採
[PUBLIC_API.md](../../docs/architecture/PUBLIC_API.md) 的 module/action/evaluator 與 lifecycle 契約。
#16 的 0.7.0 release candidate 已接入 Floating UI、Prop Physics 與原生 Ragdoll Motion；
最終候選驗證及
正式 release 尚按整合任務記錄處理。後續共用介面或 adapter 的變更仍依 PR 順序接入。

後續修改、建置與測試以 Git 工作區為準。Studio 的入口為
`scripts/node_check_version.py`、`scripts/node_build.py`、`scripts/node_run_checks.py`；
最後一個工具支援 `--list`，完整指令見 [CONTRIBUTING.md](../CONTRIBUTING.md)。
現存 `motion_codedev` 開發來源保留為唯讀參考，不做雙向自動同步。
舊 Yugo 的程式、package ID、版本及發行檔保留。
