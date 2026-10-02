# Yugo / Yugu Node 開發中心

這個 repository 是 Yugo 系列的正式程式、規範、任務與整合入口。後續新功能以 **Yugu Node Studio** 為主體；每個開發聊天使用獨立分支與工作副本，完成後經過 PR 與整合驗證。

## 目前狀態

| 項目 | 狀態 | 入口 |
| --- | --- | --- |
| 多對話開發規範與工作副本工具 | 已建立；適用後續開發 | [AGENTS.md](AGENTS.md)、[並行流程](docs/development/PARALLEL_DEVELOPMENT.md) |
| Yugu Node Studio 0.6.0 | 本機交付已完成，來源導入與測試整理待完成 | [基準盤點](docs/development/NODE_BASELINE.md)、[任務 #2](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2) |
| Node 擴充介面與模組邊界 | 設計目標，尚未實作完整 registry | [架構規範](docs/architecture/NODE_SYSTEM.md)、[任務 #3](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/3) |
| 舊 Yugo 0.1.26 | 根目錄仍是舊版 extension，保留既有建置與發布 | [舊版功能與安裝](docs/legacy/YUGO_0_1_26.md)、[Release](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/releases/tag/v0.1.26) |

Node 原始碼尚未匯入此 repository。先完成任務 #2，才開始依賴新 Node 程式的功能開發。本機完成、repository 已納入、測試合格及正式發布是不同狀態。

## 老闆與開發聊天的入口

- [任務看板](docs/development/TASK_BOARD.md)：現在該做什麼、哪些工作需要先完成。
- [開對話順序與可直接貼上的指令](docs/development/CHAT_STARTERS.md)：由技術角色自行處理分支、測試與整合。
- [GitHub Issues](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues)：任務範圍、依賴、狀態與認領紀錄。
- [Pull requests](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/pulls)：成果、驗證与整合紀錄。

所有開發聊天先讀 AGENTS.md。技術統籌分配任務，處理共用核心變更、整合與正式發布；功能聊天只修改自己的工作副本及登記範圍。任務與共用資源認領由統籌角色依序確認。

## 開發空間

| 位置 | 責任 |
| --- | --- |
| AGENTS.md | 所有聊天的共同規則 |
| docs/development/ | 任務、開工、驗證與交付 |
| docs/architecture/ | Node 介面與模組責任 |
| docs/legacy/ | 舊 Yugo 文件 |
| scripts/start_task.py | 建立分支與獨立 worktree |
| scripts/check_task_scope.py | 檢查工作位置與變更範圍 |
| .github/ | 任務與 PR 範本、規範檢查、建置及發布 |
| yugu_node_studio/ | 任務 #2 完成後的新來源目錄，目前尚不存在 |
| 根目錄 extension 檔案 | 目前保留的舊 Yugo 0.1.26 |

工作副本、Blender profile、暫存輸出與 .dev/task.json 是本機資料，不提交 GitHub。工作副本隔離檔案；介面規格與最新主線整合測試處理功能相容性。這套工具不是作業系統的權限沙箱。

## 開工與驗證

使用 Python 3.11+，從乾淨且已同步 origin/main 的 repository 建立工作副本。完整步驟在 [並行流程](docs/development/PARALLEL_DEVELOPMENT.md)。

```powershell
python -B scripts/start_task.py --task 2 --slug node-baseline --role integration --scope "yugu_node_studio/**" --scope "scripts/node_*" --scope ".github/workflows/node*.yml" --scope "blender_manifest.toml" --scope "docs/development/NODE_BASELINE.md" --workspace-root "D:/Github/Yugo-workspaces"
```

後續操作全部使用工具輸出的工作副本路徑。提交前執行：

```powershell
python -B scripts/check_task_scope.py
python -B scripts/check_version.py
python -B -m unittest discover -s scripts/tests -p "test_*.py"
```

PR 必須附上與 GitHub 任務卡一致的 task-scope 區塊。CI 檢查變更範圍與必要規範。主線保護是否啟用以 GitHub 實際設定為準；角色宣告本身不是帳號權限隔離。

## 舊版安裝與授權

舊 Yugo 使用 Blender 5.1+；[下載與功能說明](docs/legacy/YUGO_0_1_26.md) 保留完整 0.1.26 文件。Node 與舊版的 addon ID、建置來源與版本標籤分開管理；來源導入不會自動發布新 Node。

本專案採 [GPL-3.0-or-later](LICENSE)。貢獻前請閱讀 [CONTRIBUTING.md](CONTRIBUTING.md)。
