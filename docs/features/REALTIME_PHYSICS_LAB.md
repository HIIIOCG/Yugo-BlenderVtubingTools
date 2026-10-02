# 第二階段：即時物理原型

任務 [#11](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/11)，
feature 分支 `feature/11-realtime-physics`，基準
`193c33c83416d52cfc1b4184eee1feaa7b17d708`。

這個階段先驗證時間軸停止時的碰撞、施力、抓取、帶速度放手和用力吸回原位。
獨立預覽套件是 `addons/yugo_physics_lab/`，測試在 `tests/physics_lab/`；
不修改 Studio、legacy、其他任務的模組、共用 scheduler 或版本號。
套件的 `0.1.0` 只標識原型，不是 Studio 的新版本或正式發布。

## 試用

在 Blender 5.1+ 的 Install from Disk 安裝原型 ZIP，開啟生成的
`yugo-physics-lab-stage-2.blend`，或在 3D View 按 F3 搜尋
**Create Physics Lab Demo**。按 N 打開 **Physics Lab** 頁籤。

1. 按 **Start**，兩顆道具球會落地，時間軸保持原位。
2. **Apply Impulse** 對選定 Prop 施加線性與角衝量；物件會飛起、旋轉並撞牆。
3. **Grab Left / Right** 各自選取錨點附近最近的未抓取物件。移動對應 Anchor，
   物件以彈簧力跟隨；按 **Release** 保留線速度和角速度。
4. **Attract Home** 用帶阻尼的力吸向 Start 時的位置，可同時恢復朝向。
   **Maintain Attraction** 在到達後維持吸引，**Stop Attraction** 停止。
   回位途中仍會碰撞；不能穿過障礙時以 `timeout` 結束吸引，不瞬移。
5. **Apply Force** 維持指定力量，**Clear Force** 清除。**Stop** 保留當前位置；
   **Reset Home** 回到此次 Start 捕捉的原位並清掉速度、力量、抓取與吸引。

每次 Start 都捕捉新的 home；關檔重開不自動執行，也不保留未完成物理動作。
參數、collections、目標與 anchors 是持久化場景設定，運行狀態只有數字快照。
碰撞材質及物理設定在 Start 捕捉；要修改道具或障礙配置，Stop 後重新 Start。
回位參數和施力可在操作時調整。Demo 不刪除現有場景物件、不替換場景 Camera。

## 實作與所有權

- `solver.py`：純 Python，無 bpy、mathutils、外部 wheel 或網路依賴。
  狀態使用 tuple、dataclass；可從 Blender 以外執行行為測試。
- `service.py`：透過 `start/update/stop/reset/clear` 管理 Blender 輸出。
  一個預覽 world，最多 32 個動態物件；場景物件每次依名稱、pointer 和
  session identity 重新解析，不在 runtime 保存 Blender RNA。
- `preview.py`：僅在明確 Start 時註冊 60 Hz application timer；Stop 停止 timer，
  保留此次 home 與數字快照供 Reset；undo、redo、load 或 unregister 清除整個
  session。從不呼叫 frame_set 來驅動物理。
- 道具不能有 parent、object constraint、animation/driver/NLA 或 native rigid body。
  唯讀／linked 物件拒絕寫入。重複 Start 拒絕；另一個 writer 改變受控變換、
  物件刪除／取代／改名或 scene 改變時停止。錨點可以移動。
- 靜態障礙使用 Start 時捕捉的 bounding box；移動或改變障礙時需重建。
  球形半徑以場景參數為準，可用物件自訂 `yugo_physics_radius`／
  `yugo_physics_mass` 覆寫（公尺、公斤，world-space radius）。

Blender 5.1.2 的本機 RNA 檢查顯示 `RigidBodyWorld` 公開的方法只有
`convex_sweep_test`，沒有任意 step／apply-force API。因此原型使用獨立數值步進，
沒有透過更改時間軸間接驅動 Bullet。
參考：[RigidBodyWorld](https://docs.blender.org/api/3.3/bpy.types.RigidBodyWorld.html)、
[Application Timers](https://docs.blender.org/api/3.6/bpy.app.timers.html)。

## 支援範圍與後端決策

動態碰撞形狀是球；可見 Mesh 的形狀不改變 collider。靜態 Mesh 是定向 box
（evaluated bounding box），另支援無限地面及球球碰撞。這不是任意 mesh／
concave 碰撞，也沒有關節、骨架布娃娃、導航、IK 或角色姿勢輸出。

求解器固定 120 Hz，必要時細分步進限制每步位移，含速度上限、摩擦、反彈和
球形慣性。超過 0.1 秒的卡頓時間丟棄，不在下一次更新補完大段物理。
對碰撞投影可能穿過薄 box 的情況，額外保守檢查進入面，可能在圓角附近提早
阻擋。高密度堆疊、既有穿插或極端尺寸／衝量不屬於可靠接觸保證。

本階段驗證通過可證明道具互動能在停止時間軸下運行；**不能據此宣布全身
布娃娃的後端已決定**。完整 ragdoll 還需關節限制、身體 collider、姿勢接管／
恢復、碰撞穩定性及多人規模驗證；是否延伸此 solver 或使用其他後端另作決策。

正式 Studio 接入等待 #2 合格基準及 #3 公開 evaluator/action/registration/
scheduler/ownership 介面。接入時替換 preview timer，使用共用 scheduler；
不能直接操作 `_runs`／`_tasks`／`_Pulse`。回位動作 End 只在持續停穩後發出，
阻擋逾時為 Failed，取消停止該次吸引並保留其他物件的模擬。

## 可重現驗證

用任務自己的 Blender profile、artifacts 與生成場景；不安裝到使用者活躍 Blender。
下列命令從 repository worktree 執行，`<python>`／`<blender>` 為絕對執行檔路徑。

```powershell
& '<python>' -B -m unittest discover -s tests/physics_lab -p 'test_*.py'
$env:BLENDER_USER_CONFIG = '<worktree>/blender-profile/config'
$env:BLENDER_USER_SCRIPTS = '<worktree>/blender-profile/scripts'
$env:BLENDER_USER_EXTENSIONS = '<worktree>/blender-profile/extensions'
& '<blender>' --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/physics_lab/blender_service.py
& '<blender>' --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/physics_lab/blender_lifecycle.py
& '<blender>' --background --factory-startup --disable-autoexec --python-exit-code 1 --python tests/physics_lab/build_demo.py
& '<blender>' --command extension validate addons/yugo_physics_lab
& '<blender>' --command extension build --source-dir addons/yugo_physics_lab --output-dir artifacts
& '<blender>' --command extension validate artifacts/yugo_physics_lab-0.1.0.zip
& '<python>' -B scripts/check_task_scope.py
```

`blender_live_timer.py` 額外在獨立隱藏 GUI 程序測試實際 application timer，
產生 `artifacts/live-timer-report.json` 後自行關閉該程序。背景 lifecycle 測試的
手動 callback 並不能代替這項事件迴圈驗證。測試結果與效能在 PR 記錄。
ZIP、個人 profile、生成 .blend、報告與快取不提交 repository。

已驗證的實際 GUI timer 在 1.5 秒驗收窗口內更新 54 次：球從 Z=0.85 落至
Z=0.28，frame=42、subframe=0.25 保持不變，停用後 timer 清除。這是實際
排程證據，不代表 Blender 每次回呼都能精確達到 60 Hz。

32 球／4 box、300 次 1/60 秒更新的修正後效能量測：純 solver 平均 9.09 ms、
p95 9.49 ms／更新（本機 bundled Python 3.12.14）。這排除 Blender depsgraph、
RNA 寫入與 UI 費用，只證明小規模道具可行，不保證完整場景的 60 Hz。
