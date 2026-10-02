# Yugu Node Studio 0.7.0

Studio 完整程式與資產改由 GitHub 主線管理。此版本將已完成的 Floating UI 和 realtime physics 原型整合進既有 Studio node tree、menu、Action Flow 和共用 scheduler。

## 功能

- Floating UI Message / Display：事件 payload 快照、作者/文字/本機 avatar、使用 template 與 anchor 生成訊息列、平滑位移、容量回收及訊息去重。Display End 表示插入完成。
- Physics World / Prop / Attract Home / State：不推進 timeline 的即時 solver、啟停/reset、impulse/force、grab/release、吸附回位與狀態讀取。Attract Home End 只在 solver 穩定回位後發出；取消、失敗和 timeout 分別處理。
- 公共模組介面：value evaluator、action start/update/cancel、JSON event/ticket 快照、menu/cards、共用排程、lifecycle 清理與物件 transform 控制權。
- 範例可從 Blender F3 建立，供測試訊息顯示與道具物理。

## 安裝與測試

需要 Blender 5.1+。Preferences → Get Extensions → Install from Disk，選擇 `yugu_node_studio-0.7.0.zip`。啟用 Yugu Node Studio；相同 addon ID 可升級既有 Studio。開啟 Studio editor，Add menu 的 Floating UI / Physics 分類提供新節點。

舊 Studio node/socket ID 與既有圖保持。載入檔案後 runtime 維持停止，由使用者明確啟動。刪除/重接節點、mute、Undo、load 或解除外掛會清除相關執行狀態；控制同一物件的衝突寫入會被拒絕。

原型的獨立 preview timer/tree 不包含在 Studio 安裝包。現有平台服務接收端與尚在開發的 ragdoll #14 不屬於此版本。全域鍵盤實體捕捉及官方 VRM 額外素材的檢查仍需相應環境。

## 發布來源

Release 由 `studio-v0.7.0` tag 的乾淨 GitHub 原始碼建置；附件包含 installable ZIP、SHA-256 和來源 commit/file checksums。Legacy Yugo 0.1.26 使用既有 `v*` release，產品 ID 和歷史版本保留。
