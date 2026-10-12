# Contributing / 開發與交付

所有開發者與 AI 聊天先讀 [AGENTS.md](../AGENTS.md)、[並行流程](ai/PARALLEL_DEVELOPMENT.md) 與 [Node 架構](../docs/architecture/NODE_SYSTEM.md)。一項任務、一個負責聊天、一個分支、一個獨立工作副本、一個聚焦的 PR。

## 開始任務

1. 從 [GitHub Issues](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues) 選擇已具備前置條件的任務；[開工入口](ai/CHAT_STARTERS.md)提供對話順序與指令。
2. 統籌角色確認負責聊天、角色、允許路徑、共用介面與測試資源，記錄認領狀態。
3. 負責聊天自行執行 git fetch origin，核對正式 remote 與最新 origin/main；不得丟棄原有工作來同步，不要求老闆先在 GitHub Desktop 按 Pull。
4. 使用 scripts/start_task.py 建立獨立 worktree，或由統籌角色建立並記錄等效隔離環境。
5. 核對 worktree 的絕對路徑與分支；所有工具明確使用該路徑。

任務 #2 已在結構整理 #8 之後透過 [PR #15](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/15) 匯入 qualified Studio 0.6.0 基準；#3 的 qualified 公開介面已透過 [PR #18](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pull/18) 合併，契約見 [PUBLIC_API.md](../docs/architecture/PUBLIC_API.md)。#16 的 0.7.0 release candidate 已接入 Floating UI、Prop Physics 與原生 Ragdoll Motion adapter；完整候選驗證及發布狀態以整合紀錄為準。正式來源在 `addons/yugu_node_studio/`，測試在 `tests/yugu_node_studio/`。以 repository 的 worktree 為唯一可編輯來源，原 motion_codedev 來源是唯讀參考，不維持雙向人工同步。

開發中保持已記錄的基準；採用新合併依賴時再 fetch，明確整合並更新基準，重跑受影響檢查。整合角色在驗證合併候選前自行取得最新主線。閒置對話與老闆的桌面資料夾不會因其他對話更新而自動同步。

## 驗證與交付

- 執行 python -B scripts/check_task_scope.py，核對提交、暫存、未暫存及未追蹤變更。
- 修改規範或入口文件時，執行 python -B scripts/check_governance.py，檢查必要文件、連結與打包排除。
- Studio 執行 `python -B scripts/node_check_version.py`；舊 Yugo 執行 `python -B scripts/check_version.py`，再執行本次變更相關的檢查；使用 -B 避免快取。
- 修改隔離工具時，執行 python -B -m unittest discover -s tests/governance -p "test_*.py"。
- 舊版 smoke 入口是 `python -B scripts/run_blender_tests.py --blender "<Blender 執行檔絕對路徑>"`；用 `--list` 查看可選測試，未安排的 VRM／GUI 檢查記為未執行。
- 修改外掛時，使用 Blender 5.1+，驗證來源、建置、ZIP 與相關功能；測試 profile、輸出與場景使用任務自己的副本。
- Studio 獨立建置入口是 `scripts/node_build.py`，測試入口是 `scripts/node_run_checks.py`，功能測試在 `tests/yugu_node_studio/`；兩個工具都使用任務專屬 Blender profile 與輸出。Windows 舊版建置入口 `scripts/build.ps1` 及 `scripts/check_version.py` 繼續只處理 `addons/yugo/`，舊版測試在 `tests/yugo/`。
- PR 使用範本，附上任務 ID、角色、允許路徑、依賴、實際測試與相容性影響。未通過的檢查列為問題，不把略過當成通過。
- 統籌角色更新至最新主線，驗證實際合併候選，再依序合併。普通功能聊天不建立正式 tag 或覆寫既有 Release。

Studio 的 PowerShell 建置與完整驗證入口：

```powershell
python -B scripts/node_check_version.py
python -B scripts/node_build.py --blender "<Blender 執行檔絕對路徑>"
$StudioVersion = python -B scripts/node_check_version.py --print-version
python -B scripts/node_run_checks.py --blender "<Blender 執行檔絕對路徑>" --package "dist/yugu_node_studio-$StudioVersion.zip" --output artifacts/node-checks
python -B scripts/node_run_checks.py --list
```

建置驗證來源、ZIP、完整部署檔案清單與內容；測試核對實際載入的 repository 來源及生成範例。官方 VRM extension 或真人全域鍵盤驗收需要的項目，依 [Node 基準](../docs/development/NODE_BASELINE.md) 明確記錄 prerequisite 與未執行範圍。Studio tag 採 `studio-v<version>`；正式發布由 integration 角色依 [Studio release 流程](../docs/development/STUDIO_RELEASE.md) 驗證路由後執行，legacy 的 `v*` 路由保留。

不提交生成 ZIP、Python cache、Blender recovery、個人 profile、.dev/、工作副本或本機連線設定。必要的受控 .blend 資產與測試 fixture 可以提交，但必須說明用途。

動作、角色與 Blender 測試檔依 [素材準備與交接](../docs/development/TEST_MATERIALS.md)處理；原件唯讀，測試使用自己的副本。大型或私有素材先留在本機／指定共享儲存，不自行上傳。

Bug 報告請附產品與版本、Blender 版本、作業系統、重現步驟與必要錯誤輸出。移除私有 avatar 與網路資訊。

By contributing, you agree that your contribution is licensed under GPL-3.0-or-later.
