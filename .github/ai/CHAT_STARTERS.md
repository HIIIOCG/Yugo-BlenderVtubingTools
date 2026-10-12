# 開對話的順序與可貼上的指令

你只要把對應指令貼給聊天。更新程式、建立獨立工作副本、測試與交付由工作
聊天處理，不需要你一直手動 Pull。準備測試檔案請看
[測試素材](../../docs/development/TEST_MATERIALS.md)。

任務狀態與允許修改範圍，以 [GitHub Issues](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)
的最新內容為準。每個聊天開工時自行 `fetch origin`、核對最新 `origin/main`，
讀取 `AGENTS.md` 與自己的 Issue，再建立獨立 branch／worktree；不得覆蓋既有工作。
開發中保留登記的基準，採用新依賴或交付整合時再更新並驗證。

## 已完成：#2 Node 基準整合

結構 #8 與來源導入 #2 已完成；[PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15)
將 qualified Studio 0.6.0 基準合併到 GitHub。來源在 `addons/yugu_node_studio/`，
測試在 `tests/yugu_node_studio/`，驗證證據與未執行項目見 [NODE_BASELINE.md](../../docs/development/NODE_BASELINE.md)。
後續聊天從 GitHub 主線建立自己的 worktree，原 `motion_codedev` 資料夾保持唯讀參考。

## 已完成：#3 公開介面；後續核心與測試

#3 已由 [PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18)
完成 qualified 合併。[PUBLIC_API.md](../../docs/architecture/PUBLIC_API.md) 定義功能宣告、
value／event／action、cards、共用排程、lifecycle 與 transform claims。
#16 的 0.7.0 release candidate 已接入 Floating UI、Prop Physics 與原生 Ragdoll Motion；
最終候選驗證與
正式發布另依整合紀錄處理。不要重新認領已完成的 #3。

先查看 Issue 的最新認領狀態；已有人負責的任務不另建重複工作。
兩個聊天使用各自的工作副本，採用同一個已合併基準並登記後續依賴。

**下一個 Node 核心改動聊天（先建立新 Issue）：**

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的核心改動任務 #<新 Issue 編號>，角色為 core。自行 fetch，讀最新 AGENTS.md、該 Issue、docs/architecture/NODE_SYSTEM.md、docs/architecture/PUBLIC_API.md 與 docs/development/NODE_BASELINE.md；確認任務認領與依賴後，按最新 Issue scope 從合併主線建立獨立 worktree。先登記要改變的公開契約、既有消費端與相容方式；使用 repository 的 Studio 建置及測試入口驗證受影響功能後交 PR，由整合角色整合。

**Node 測試聊天：**

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #4，角色為 feature。自行 fetch，讀最新 AGENTS.md、Issue #4、docs/architecture/PUBLIC_API.md、docs/development/NODE_BASELINE.md 與 docs/development/TEST_MATERIALS.md；確認任務認領與依賴後，按最新 Issue scope 在自己的 worktree 維護 tests/yugu_node_studio/，使用獨立 Blender profile、場景複本與輸出。從 Git 的 Studio 來源建置安裝包，驗證 lifecycle、保存重開、value／event／action、取消／失敗與並行，記錄實際通過及受限項目；需要核心或 CI 修改時列出依賴，最後交聚焦 PR。

#4 依最新 Issue 状態驗證既有行為與已合併的公開 API。
後續新功能先閱讀實際契約，再由整合角色確認各自的 Issue、scope 與依賴。

## Studio 功能整合與建置

已完成的功能 PR 由整合角色確認核心依賴、Studio adapter 與原功能的驗證，再接入
正式 Studio 候選。目前 #16 的 0.7.0 release candidate 已接入 Floating UI、Prop Physics
與原生 Ragdoll Motion；
最終完整驗證和正式發布不能只由此文件推定。可把以下指令交給負責 integration
的聊天，替換成已登記的任務編號：

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的 Studio 整合任務 #<Issue 編號>，角色為 integration。自行 fetch 最新 GitHub 主線，讀 AGENTS.md、該 Issue 的 scope、Node 基準與待整合功能的 PR。於自己的 worktree 依序接入已 qualified 的核心及功能 adapter，驗證現有 Studio 圖與新功能、建置並測試實際 ZIP，交付含來源 commit 的聚焦 PR。正式 release 依該 Issue 的授權與發布流程處理；原共享來源保持唯讀。

Studio 版本、建置與完整測試入口為 `scripts/node_check_version.py`、
`scripts/node_build.py`、`scripts/node_run_checks.py`；完整指令見
[CONTRIBUTING.md](../CONTRIBUTING.md)。測試清單可用 `python -B scripts/node_run_checks.py --list` 查看。

## 整合與任務紀錄

整合角色依序確認認領、共用資源、合併與正式發布。既有 GitHub 管理聊天可
承擔此角色；其他聊天須有你的授權，並在 Issue 記錄一致的角色與範圍。
每張任務卡保留成果、負責聊天、scope、依賴、branch／工作區、基準 commit、
測試資源、狀態及 PR。PR 使用 Issue 的相同 `task-scope` 宣告。

前置工作沒完成就標記等待；整合前自行取得最新主線並測試實際候選。
文件不會自動傳訊或分派給其他聊天，你貼上指令後，聊天才開始自己的任務。
