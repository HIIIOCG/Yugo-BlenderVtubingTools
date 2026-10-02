# 開對話的順序與可貼上的指令

你只要把對應指令貼給聊天。更新程式、建立獨立工作副本、測試與交付由工作
聊天處理，不需要你一直手動 Pull。準備測試檔案請看
[測試素材](TEST_MATERIALS.md)。

任務狀態與允許修改範圍，以 [GitHub Issues](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)
的最新內容為準。每個聊天開工時自行 `fetch origin`、核對最新 `origin/main`，
讀取 `AGENTS.md` 與自己的 Issue，再建立獨立 branch／worktree；不得覆蓋既有工作。
開發中保留登記的基準，採用新依賴或交付整合時再更新並驗證。

## 先做：#2 Node 基準整合

建議交給既有「整理 Yugo 0.1.14 GitHub 專案」聊天，或新開「Node 基準整合」。
使用 Git repository 專案 `D:/Github/Yugo-BlenderVtubingTools`，請貼上：

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #2，角色為 integration。自行 fetch 最新主線，讀 AGENTS.md、最新 Issue #2 與 docs/development/NODE_BASELINE.md；依 Issue scope 建立獨立 worktree 並記錄基準。確認原 Node 聊天已停止變動後，唯讀匯入 Studio 0.6.0，保留 package／node／socket 身分，處理已知測試與跨舊安裝包依賴，完成獨立建置、驗證、PR 與最新主線整合。不得修改原來源或發布 tag。你可處理已授權範圍內的例行技術決策。

## #2 完成後，可同時做：#3 核心與 #4 測試

兩個聊天都要先確認 #2 已合併且基準通過驗證，使用各自的工作副本。

**Node 核心架構聊天：**

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #3，角色為 core。自行 fetch，讀最新 AGENTS.md、Issue #3 與 docs/architecture/NODE_SYSTEM.md；確認 #2 合格後，按最新 Issue scope 建立獨立 worktree，向整合角色登記依賴與共用介面。實作最小公開擴充介面，保留既有圖形相容性；驗證後交 PR，由整合角色合併及發布。

**Node 測試聊天：**

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #4，角色為 feature。自行 fetch，讀最新 AGENTS.md、Issue #4 與 docs/development/TEST_MATERIALS.md；確認 #2 合格後，按最新 Issue scope 建立獨立 worktree、Blender profile、場景複本及輸出目錄。驗證 lifecycle、保存重開、value／event／action、取消／失敗與並行，記錄實際通過及受限項目；需要核心或 CI 修改時列出依賴，最後交聚焦 PR。

#4 可先驗證既有行為；涉及 #3 新介面的測試，等介面確認後再更新。
後續新功能等公開介面確定，再由整合角色建立各自的 Issue。

## 整合與任務紀錄

整合角色依序確認認領、共用資源、合併與正式發布。既有 GitHub 管理聊天可
承擔此角色；其他聊天須有你的授權，並在 Issue 記錄一致的角色與範圍。
每張任務卡保留成果、負責聊天、scope、依賴、branch／工作區、基準 commit、
測試資源、狀態及 PR。PR 使用 Issue 的相同 `task-scope` 宣告。

前置工作沒完成就標記等待；整合前自行取得最新主線並測試實際候選。
文件不會自動傳訊或分派給其他聊天，你貼上指令後，聊天才開始自己的任務。
