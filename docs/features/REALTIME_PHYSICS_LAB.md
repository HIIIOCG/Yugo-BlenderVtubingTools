# 即時物理原型：歷史紀錄

任務 [#11](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/11) 的
`yugo_physics_lab` 0.1.0 曾提供時間軸停止時的球形道具碰撞、施力、抓取、
帶速度放手與吸回原位。原型基準為
`193c33c83416d52cfc1b4184eee1feaa7b17d708`，原始實作與驗證仍可由 Git 歷史取得。

任務 [#38](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/38) 依使用者指示，
以獨立的 **Yugo PhysX Tracker 0.4.0 / ProtoMotions** 取代 repository 中的
`addons/yugo_physics_lab/` 與對應測試。安裝、來源與驗證入口見
[PhysX Tracker](PHYSX_TRACKER.md)。

這是獨立套件的替換，不是原 Physics Lab 的功能升級或儲存資料遷移。
舊 `Scene.yugo_physics_lab` 設定、demo、Grab Left/Right 與 Attract Home 操作
不會自動轉成 ProtoMotions 節點。需要繼續執行舊場景時，保留原檔並在自己的
隔離環境使用原型的歷史版本；新場景依 Tracker 的骨架、控制器與物理節點重新建立。

Yugo 內建 MuJoCo 物理、Studio 的 Prop Physics / Ragdoll 模組與獨立
`yugo_ragdoll` 不屬於此次替換範圍。既有 Release、tag 與資產保留。
