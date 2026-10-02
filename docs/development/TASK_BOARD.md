# 開發任務看板

GitHub Issue 是任務狀態與認領紀錄的唯一管理來源。本頁是入口與先後順序，狀態以連結 Issue 的最新內容為準。

| 順序 | 任務 | 角色 | 前置條件 |
| --- | --- | --- | --- |
| 0 | [#1 開發空間與規範](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/1) | integration | 本次整理 |
| 1 | [#2 Node Studio 0.6.0 基準導入](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/2) | integration | #1 完成 |
| 2A | [#3 Node 共用擴充介面與模組邊界](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/3) | core | #2 完成 |
| 2B | [#4 Node 回歸測試與 Blender 隔離驗證](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/4) | feature（測試） | #2 完成，可與 #3 並行 |

功能聊天在 #2 完成前可唯讀研究；在公開介面尚未確認前，不各自修改共享核心。後续功能由統籌角色另建聚焦任務，不預先承諾未研究的設備或物理後端。

## 任務卡必備資料

目標／完成條件、負責聊天、角色、允許路徑、共享介面、依賴、分支、worktree 絕對路徑、起始 commit、資源預約、狀態、PR 與測試結果。

統籌角色在 Issue 補上一個 task-scope JSON，PR 使用完全相同的宣告。開工前由統籌依序確認認領，避免聊天同時自派任務。Issue 不具備原子租約後端；它的認領由統籌角色序列化。

狀態依序為待定 → 可認領 → 開發中 → PR → 待整合 → 已合併；正式發布另記錄版本與 tag。前置任務未完成則標等待，不當成故障或完成。

## 整合角色

原有聊天「整理 Yugo 0.1.14 GitHub 專案」已負責過 GitHub 同步、CI 與 Release。今後可由該聊天承擔整合角色，先讀新的 AGENTS.md，停止舊式雙向來源同步。任何其他聊天只有在老闆授權為統籌／整合且任務卡記錄一致時才承擔此角色。

認領與消息不會因本文件而自動傳到其他對話；老闆把相應指令貼給對話後，由對話讀取 GitHub 的最新任務資料。
