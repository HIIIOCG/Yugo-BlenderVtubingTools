# 開對話的順序與可直接貼上的指令

老闆只需要指定任務目標；技術角色負責環境、分支、測試與交付。以下指令可貼進新對話，或貼給已有的 GitHub 管理對話。不需要讓所有對話指向同一個可寫的 motion_codedev 資料夾。

## 先開：Node 基準整合

建議交給既有「整理 Yugo 0.1.14 GitHub 專案」聊天，或新開一個名為「Node 基準整合」的聊天。選擇 Git repository 專案 D:/Github/Yugo-BlenderVtubingTools；以 origin/main 建立自己的 worktree。先保留原資料夾未提交的 camera.py，禁止覆蓋或丟棄。

直接貼上：

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #2「Node Studio 0.6.0 基準導入與測試可重現化」，角色為 integration。先讀取 GitHub 最新 AGENTS.md、README、Issue #2 與 docs/development/NODE_BASELINE.md，核對正式 repository。保留任何既有未提交修改；在最新 origin/main 的獨立 worktree 開工，記錄工作位置、基準與允許範圍。唯讀匯入 D:/Projects/BCON26/motion_codedev/yugu_node_studio，先確認來源對話已停止變動；保存 Node 身分、節點／socket ID 與 0.6.0 行為，調查已知測試契約與跨舊 ZIP 依賴，建立乾淨環境驗證與獨立 Node 建置。不要修改原來源資料夾或發布 tag。完成檢查、PR 與最新主線整合後更新基準狀態。你有權處理本任務的例行技術決策、修正與 GitHub 整合；只有需要改變產品目標或超出授權時才向我詢問。

## #2 完成後，可同時開：Node 核心架構

直接貼上：

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #3「Node 共用擴充介面與模組邊界」，角色為 core。先讀最新 AGENTS.md、Issue #3 及 docs/architecture/NODE_SYSTEM.md，確認 #2 已合併且 Node 基準 qualified。向整合角色登記負責範圍與共享介面，用獨立分支／worktree 實作最小公開擴充介面，保存已存在的 Node 行為與圖形相容性。功能模組不直接操作私有 workflow 狀態；共享 schema、scheduler、registration 與 lifecycle 由你統一維護。只在 Issue 的允許範圍修改，相關測試通過後交 PR；整合與正式發布由統籌角色處理。

## #2 完成後，可同時開：Node 測試與驗證

直接貼上：

> 你負責 HIIIOCG/Yugo-BlenderVtubingTools 的任務 #4「Node 回歸測試與獨立 Blender 驗證流程」，角色為 feature（測試）。先讀最新 AGENTS.md 與 Issue #4，確認 #2 已合併。用自己的獨立 worktree、Blender profile、場景副本與輸出目錄，只修改 yugu_node_studio/tests/** 與 docs/development/NODE_TESTING.md。覆蓋 Node lifecycle、保存重開、value/event/action completion、取消／失敗與並行，記錄自動測試和真人桌面／設備受限項目的區別。不要順手修改核心或 CI；需要的核心修正与CI命令交給對應責任人，最後提交聚焦 PR。

## 後續新功能

待公開介面確認後，統籌在 GitHub 建立個別功能 Issue，指定模組、介面、完成條件與資源。老闆再把 Issue 交給獨立對話。每個對話開工時都重新核對工作副本路徑，不把「同一專案中的不同分頁」當成檔案隔離。
