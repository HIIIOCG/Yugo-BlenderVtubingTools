# Studio build 與 release

Studio 來源為 GitHub `addons/yugu_node_studio/`；部署 ID 固定 `yugu_node_studio`。本機共享原始資料夾只作歷史參考。Legacy `yugo` 的版本與 release 保留。

## 從 GitHub 建置供測試

在乾淨 clone 或獨立 worktree 選定已合併的 commit，設定 task-specific Blender user 路徑後：

```text
python -B scripts/node_check_version.py
python -B scripts/node_build.py --require-clean --blender <absolute-Blender-executable>
python -B scripts/node_run_checks.py --blender <absolute-Blender-executable> --package dist/yugu_node_studio-<version>.zip --output artifacts/node-checks
```

建置腳本隔離自己的 profile；regression runner 再為每個 Blender process 隔離 profile/暫存目錄。產物是可安裝的 extension ZIP、SHA-256 和 `studio-build.json`，後者記錄確切來源 commit、dirty 狀態、每個部署檔案的 checksum。測試有 prerequisite 的項目應照結果列為未執行。

Blender 5.1+：Edit → Preferences → Get Extensions → 右上選單 → Install from Disk，選擇 ZIP。啟用 Yugu Node Studio，開啟 Yugu Node Studio editor。已有 Studio 安裝使用相同 package ID 升級。先保存自己的 .blend；不把 test launcher 或 development preview 當成 Studio 安裝包。

## 使用者測試後發布

整合角色確認測試結果與 final main commit，metadata 中兩個 version 一致，並依
[release-writing rules](RELEASE_NOTES.md) 準備英文公開正文
`docs/releases/STUDIO_<version_with_underscores>_NOTES.md`。正文只放實際安裝包
下載、精簡的節點／功能變更及一行相容性；Studio 目前只有主安裝包，不增加
不存在的選用套件連結。既有 `STUDIO_<version_with_underscores>.md` 及
qualification 文件保留工程與驗收紀錄，不再直接作為公開正文。

發布前執行下列檢查；成功時輸出已檢查的 repository 相對正文路徑：

```text
python -B scripts/check_release_notes.py --product studio --tag studio-v<version>
```

完成候選驗證與正文檢查後，整合角色依任務授權建立新的 `studio-v<version>` tag 並推送。

`.github/workflows/node-release.yml` 會核對 tag、main ancestry、未存在同名 release，檢查並直接讀取專用 `_NOTES.md` 正文，從 tag 的乾淨原始碼建置並完成全套 portable regression / ZIP 安裝測試，最後建立 GitHub Release。附 ZIP、checksum 和 build provenance；後兩者不追加至正文。失敗時不建立 release，保留 diagnostic artifacts。

Studio tags 是 `studio-v*`；legacy tags 是 `v*`，兩條 workflow 不互相匹配。不重用既有 tag 或替換既有 asset；安裝包修正使用新版本。已授權的正文修訂保留 tag 與附件，也不授權新的發布。整合測試包完成或已有正文檔案不代表正式 release 已發布。
