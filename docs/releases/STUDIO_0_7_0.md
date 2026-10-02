# Yugu Node Studio 0.7.0 release candidate

Blender 5.1.2／5.2.0 LTS 的完整 portable suite、實際 ZIP 載入、真實 GUI 排程及
Undo／Redo 已通過；來源、驗證證據與未執行項目見
[0.7.0 qualification](STUDIO_0_7_0_QUALIFICATION.md)。正式 tag／Release 尚未發布。

Studio 完整程式與資產改由 GitHub 管理。此候選將 Floating UI、realtime prop physics 與 #14 的原生 Ragdoll Motion，透過 #3／PR #18 的 [公開 API](../architecture/PUBLIC_API.md)，整合進既有 Studio node tree、menu、Action Flow 和共用 scheduler。完整候選驗證及正式發布尚待整合紀錄確認。

## 功能

- Floating UI Message / Display：事件 payload 快照、作者/文字/本機 avatar、使用 template 與 anchor 生成訊息列、平滑位移、容量回收及訊息去重。Display End 表示插入完成。
- Prop Physics：Physics World、Physics Impulse、Physics Force、Physics Grab、Physics Release、Attract Home、Stop Attraction、Physics State；不推進 timeline 的即時 solver、啟停/reset、吸附回位與狀態讀取。Attract Home End 只在 solver 穩定回位後發出；取消、失敗和 timeout 分別處理。
- 公共模組介面：value evaluator、action start/update/cancel、JSON event/ticket 快照、menu/cards、共用排程、lifecycle 清理與物件 transform 控制權。
- Ragdoll Motion：typed Pose Source → Ragdoll Motion → Apply Ragdoll；Start／Enter、Hit、Recover、Stop、Cancel 明確操作。Enter／Recover 只在 grounded/stable gate 與 recovery blend 達到實際 COMPLETED 後發出 End；Hit 的 End 表示 impulse 已加入。Busy policy 僅套用 Enter，其他命令仍可操作。Completed 保留 lease 供再次 Hit，Stop 還原仍由它控制的 transforms 並釋放。
- F3 範例：Create Studio Floating UI Example／Create Studio Physics Example／Create Studio Ragdoll Example。Ragdoll 保留原始 21-bone 範例與預設參數。

## 安裝與測試

需要 Blender 5.1+。Preferences → Get Extensions → Install from Disk，選擇 `yugu_node_studio-0.7.0.zip`。啟用 Yugu Node Studio；相同 addon ID 可升級既有 Studio。開啟 Studio editor，Add menu 的 Floating UI / Prop Physics / Ragdoll Motion 分類提供新節點。

舊 Studio node/socket ID 與既有圖保持。載入檔案後 runtime 維持停止，由使用者明確啟動。刪除/重接節點、mute、Undo、load 或解除外掛會清除相關執行狀態；控制同一物件的衝突寫入會被拒絕。

原型的獨立 preview timer/tree 不包含在 Studio 安裝包。平台服務接收端仍是後續工作；ragdoll #14 的整合與驗證列入本候選。全域鍵盤實體捕捉及官方 VRM 額外素材的檢查仍需相應環境。

Ragdoll 啟動前需先停止並釋放既有 Apply／native pose／spring controller。整個 ARMATURE
lease 同時獨占物件 transform 與所有 pose 的寫入控制權；成功捕捉後，釋放由該 rig 的 physics anchors
持有的 props，無關 anchors 保持。Ragdoll 最多 128 selected bones；recovery 為 kinematic
blend，animated/IK-driven、linked、constrained、mirrored、sheared 或 nonuniformly scaled rig
不接受。各功能的完整操作與限制見 [安裝說明](../../addons/yugu_node_studio/README.md)。

## 發布來源

正式發布規劃由 `studio-v0.7.0` tag 的乾淨 GitHub 原始碼建置；驗證通過後，附件包含 installable ZIP、SHA-256 和來源 commit/file checksums。目前為 release candidate，尚不代表 tag 或 Release 已發布。Legacy Yugo 0.1.26 使用既有 `v*` release，產品 ID 和歷史版本保留。
