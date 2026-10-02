# Yugu Node Studio qualified 基準

日期：2026-10-02。任務 #2 已完成來源導入與本機 qualification；GitHub PR/CI 和主線合併是下一個整合關卡。

## 固定來源

- Runtime/source commit：`b87004c85694f80176eb13823685fbdb53a8b390`。
- 來源版本 0.6.0、package ID `yugu_node_studio`、最低 Blender 5.1.0。
- 一次性來源：`D:/Projects/BCON26/motion_codedev/yugu_node_studio`，其 35 個部署檔案與已交付 ZIP 逐一 byte-identical。
- 原始 ZIP SHA-256：`53b6dad3873bdb973b806165837c90c1bae02f50a2c093524b1bea2bb0635379`。
- 匯入保留全部 runtime、node/socket ID、資產與版本；唯一新增部署檔案為 GPL LICENSE。來源 commit 的執行檔未為測試改寫。
- preset.blend SHA-256：`f5ceee56404def007f6a3c25115efd314cea4abdb0d98cb8f8291e874151e5af`；測試從受控 checksum 驗證，不再依賴舊 Yugo ZIP。
- Git 中 `addons/yugu_node_studio/` 是後續可編輯來源；原始共享資料夾保持唯讀參考。`yugu_node` 不是 runtime 依賴。

## 可重現入口

```text
python -B scripts/node_check_version.py
python -B scripts/node_build.py --blender <absolute-Blender-executable>
python -B scripts/node_run_checks.py --blender <absolute-Blender-executable> --package dist/yugu_node_studio-0.6.0.zip --output artifacts/node-checks
python -B scripts/node_run_checks.py --list
python -B scripts/check_task_scope.py
```

建置驗證 source、ZIP、完整部署 file set 和每個檔案的 bytes。測試驗證實際載入 repository 路徑，使用獨立五個 Blender user 路徑、暫存目錄、輸出目錄及 loopback ephemeral ports。demo/saved 的共用生成檔在該次測試輸出目錄，按 demo → saved 執行。

## 實際 qualification

| 檢查 | 結果 |
| --- | --- |
| Blender 5.2.0 LTS 全套 portable regressions | 25 個 Blender scripts 全部通過 |
| Blender 5.1.2 全套 portable regressions | 同 25 個 scripts 全部通過 |
| standalone protocol suite | 4 通過、2 需 mathutils 明確略過 |
| Blender protocol suite | 全 6 通過 |
| source validate / build / ZIP validate | 5.1.2 與 5.2.0 全部通過，實際 ZIP 安裝/註冊測試通過 |
| Studio 與 tracked legacy 同時註冊、重載 | 通過 |
| 舊圖、workflow/cards、cancel、Undo、load、demo save/reload | 全套涵蓋並通過 |
| governance / legacy metadata / task scope | 通過；legacy 維持 0.1.26 |
| governance unittest | 54 tests：53 通過、1 平台相關略過 |
| legacy source / build / ZIP validate | 通過；新 Studio 原始碼未混入 legacy ZIP |

新基準 ZIP（含 LICENSE）：344927 bytes；SHA-256 `340523e946eb6171effa888e1f20343e54efc00572739b40fdfae7625d20995f`。不提交生成 ZIP/profile/log/example blend。

舊 `blender_integration.py` 斷言已依現行 Trigger/Apply 契約更新，沒有恢復 retired Character Output scheduler。Shapes selected 使用現行 Enter socket。測試從 manifest 讀取版本，範例不再把 0.6.0 寫死。

## 明確未執行項目

- 真實桌面全域鍵盤捕捉／按鍵注入：須真人驗收；自動測試使用合成事件，實體操作需 `YUGUS_TEST_NATIVE_KEYBOARD=1`。
- `blender_vrm_snapshot.py`、`blender_outline_multi_material.py`：需另外供應官方 VRM extension，未列入 portable 通過數。
- `blender_shapes_diagnostic.py`：保留行為的診斷腳本，非 passing regression。

## 後續整合

`.github/workflows/node-ci.yml` 對 Studio 與 tracked legacy 消費端的變更執行上述 build/portable suite。Studio 專用版本檢查僅接受 `studio-v<version>`；既有 `v*` workflow 仍只發布 legacy。此基準任務不建立 release tag。#3 在 qualified 基準合併後建立公共模組介面，完成 PR #12/#13 的功能再經 Studio adapter 整合；#14 尚在開發，獨立追蹤。
