# Yugo 與 DEV Node 融合紀錄

任務 #32，日期 2026-10-10。這份紀錄描述本次本機整合候選；不代表已發布、
已合併主線或已更新使用者目前的 Blender。

## 使用者指定的整合方式

- 將 DEV 的 Yugo Node 功能接入既有 `yugo` extension，同一次安裝與啟用。
- 既有控制介面與 Node 控制介面都使用同一個 N-panel **Yugo** 標籤。
- 重複的 Comp 與 `preset.blend` 採用舊版。保留舊版合成、材質最佳化與
  outline 實作及其資產，不以 DEV preset 覆蓋。
- 根 README 已依使用者前一步要求採用 DEV README。

## 固定來源與相容性

| 來源 | Commit | 匯入範圍 |
| --- | --- | --- |
| Yugo-BlenderVtubing-Dev | `fb1391c0a76895be64422c9b62c88a8a924e0a72` | `addons/yugo_node/` 的 0.10.0 Node 功能 |
| Yugo-BlenderVtubingTools | `659970aec0eae7a734018b34839cb29fabe1a9bf` | `addons/yugo/` 的舊版功能與資產 |

整合工作副本：`D:/Github/Yugo-BlenderVtubing-Dev/dev/dist/worktrees/32-yugo-node-fusion`。
分支：`feature/32-yugo-node-fusion`。來源放在 `addons/yugo/node/`；使用者目前
Blender 的 DEV 載入路徑不在本次例行驗證中更動。

保留 `yugo` package ID。Node 的 tree/node/socket/operator IDs 及持久化欄位
沿用原 0.10.0，以保留舊 `.blend` 圖的讀取方式。原版本已退休的 Chat、
Keyboard & State、Actions、Parallel、Wait All、Float Receiver 與 Legacy
Spring 宣告仍作為 inactive compatibility nodes 保留，不恢復其背景執行或
選單入口。

`addons/yugu_node_studio/` 仍保留原始獨立產品。因為它與內嵌 Node 具有相同
Blender 類別識別，本次整合加入雙向註冊 guard：任一套已註冊時，另一套
啟用應回報清楚錯誤並保留已啟用套件，避免取代 RNA class。Guard 與雙向
測試結果列入下方候選驗證，不把來源共存視為同時啟用已可安全運作。

## 共用 Comp 與資產

`addons/yugo/preset.blend` 必須 byte-identical 保留。SHA-256：

```text
a477e4417bf89877346beab2b720ddfd8959bf72f82b475631718372d691e119
```

舊 preset 含 `_CompCustom`、`_MToonCustom` 與 `Black_OutlineFast`；DEV
preset 新增的 Floating UI 不能靠舊 preset 匯入。Node 的 Composer 介面因此
接向父套件的 Comp runtime、`yugo_composing` 與舊角色集合；MToon/outline
也使用父套件模組及父套件 preset，不再註冊第二套材質同步系統。

`addons/yugo/node/assets/floating_ui.blend` 是從 DEV preset 匯出的獨立 library。
只 append 最高層 `Floating UI` Collection，驗證 `UI_Style`、`UI_3D`、
`UI_Transform`、`ChatText`、`UserName`、`ChatPanel`、`UserIcon` 及 `UserPanel`
綁定，再將圖片打包；以 `bpy.data.libraries.write` 寫出該 Collection 及
Blender 自動收集的依賴。不包含 `_CompCustom`、任何 `_MToonCustom` 材質或
`Black_OutlineFast`，也不寫出來源測試場景與角色動作。

`chat_ui_assets.import_floating_ui` 的 public API、owner/role tags、重複匯入
重用與 rollback 行為保留，預設 library 路徑改為這份獨立資產。隨包仍保留
`assets/User_icon.jpg`；library 已 packed 的圖像不依賴來源電腦檔案路徑。
目前 0.10.0 的 Chat 操作維持退休狀態，資產保留不代表重新啟用未完成功能。

本次以 Blender 5.1.2 匯出，來源 DEV preset SHA-256：
`92a511c5959bd81fb830ed234b6d022443cac484a99dee1e31678a11a94c8a40`。
Floating UI library 為 83,135 bytes，SHA-256：
`c2088ebf9ffe4ba72e9f79388df0bc18ba47622a96611417c3efc7815e41f58c`。
內容只有 3 個 Collection、5 個 UI Object、3 個 UI Material、1 張 packed
image，沒有 node group。匯出腳本與報告保存在此工作副本的
`artifacts/fusion-assets/`，不列入 extension 發布內容。

## 依賴與授權檔案

Node 的 8 個官方 wheels 以原始 bytes 保留在 `addons/yugo/node/wheels/`，
Yugo manifest 的 wheel paths、Windows x64 平台與權限聲明需對應實際
部署內容。MuJoCo 使用 Blender 的 NumPy；本次不另外安裝或取代 NumPy。

保留 Node 的 `LICENSE-MUJOCO.txt`、`NOTICE-MUJOCO.txt`，及各 wheel 內的
原始授權、authors/notice 檔。MuJoCo wheel 內的 `LICENSES_THIRD_PARTY.md`
也隨完整 wheel 保留。Yugo 根目錄與 add-on 的 GPL LICENSE 保留；兩者原始
hash 因行尾不同而不同，但正規化行尾後內容相同。

## 候選驗證

測試只使用工作副本內的獨立 Blender profile、暫存目錄、輸出與場景副本。
不操作、安裝、重載或儲存使用者目前的 Blender 場景。Blender 5.1.2 與
5.2.0 LTS 各完成 14 項 source 及同 14 項實際 ZIP 檢查，共 56 項全數通過。
兩輪都通過完整部署檔案清單與逐檔 bytes 比對，且沒有測試途中來源變動。

| 檢查 | 結果 |
| --- | --- |
| 固定來源與舊 preset bytes/hash | 兩輪 source/ZIP preflight 通過；部署檔案與 ZIP 逐一 byte-identical；source 與 ZIP 的舊 preset SHA-256 保持 `a477e4417bf89877346beab2b720ddfd8959bf72f82b475631718372d691e119` |
| Floating UI library 依賴、packed image 與匯入重用 | Blender 5.1.2 背景 probe 通過：正確綁定、packed image、改名後重用、不同 owner、linked Style 在 append 前拒絕；退休狀態保留 |
| Yugo/Node 註冊、解除與重複啟停 | 兩版 Blender、source/ZIP 都通過；121 個 Node/Socket/Tree 類別完整註冊，65 個可用節點逐一建立；含連結圖 save/load 與兩次完整 package reload |
| 部分註冊失敗 rollback | lifecycle 在類別、屬性與 scheduler 已建立後注入子模組失敗；回收 classes/properties/handlers/timers，恢復實作後可重試啟用；四種組合皆通過 |
| 全部 N-panel category 與單一 Comp/MToon runtime | 四種組合皆通過：18 個已註冊 UI panels 只有 `Yugo` category，涵蓋 VIEW_3D/NODE_EDITOR；Node 共用父套件 Comp、MToon、outline 與角色集合，不建立 `yugus_composing`／`yugus_avatars` |
| 原 Node 回歸與舊版相關回歸 | 四種組合的 14 項皆通過：lifecycle、release_scope、node_menu、save_lifecycle、receiver_reconnect、physics_live、visual_compatibility、math_ops、scheduler、load_safety、composing、outline_preset、mtoon_optimize、outline_bake；math_ops 含 17 個單元測試 |
| Node／獨立 Studio 雙向註冊拒絕 | 四種組合的 owner guard 測試皆通過：Yugo 先啟用與獨立 Studio 先啟用時，另一套都明確拒絕註冊；原 owner、handlers、timer 及圖功能保持 |
| 舊 visual tags 相容性 | 四種組合皆通過：讀取 `yugo`／`yugus` shader 與 Comp generation tags 不改寫既有結構／tags；明確 Optimize/Composer Reload 才以舊 preset 重建，不累積節點、不重套 Shadow Mix，保留效果數值及使用者 Viewer |
| extension validate/build 與實際 ZIP 註冊 | `artifacts/fusion-build/build.json` 為 passed；原 `scripts/build.ps1` 完成 source validate、build 與 ZIP validate；兩版 Blender 的完整 ZIP 註冊／回歸通過 |
| task scope、版本與治理檢查 | `scripts/check_task_scope.py`、`scripts/check_version.py`、`scripts/check_governance.py` 通過；Yugo 版本保留 0.1.26 |

本機候選安裝包：`dist/fusion/yugo-0.1.26.zip`，10,410,003 bytes，SHA-256：
`867d745cf8808418481db828f4200584f4f49c676d58382aa8aa280ec7e38880`。
它是本次測試實際解壓與載入的 ZIP；不是已發布的新版本。

完整結果在 `artifacts/fusion-final-51/results.json` 與
`artifacts/fusion-final-52/results.json`。各報告包含實際命令、測試 snapshot
hash、source/ZIP hashes 與每個 `source/<test>/test.log`、
`package/<test>/test.log` 路徑；生命週期統計位於各 `lifecycle/test.log` 的
`FUSION_LIFECYCLE_OK`，visual tags 結果位於 `visual_compatibility/test.log`。
建置命令與完整 log 在 `artifacts/fusion-build/build.json`、`build.log`。

以下命令從本工作副本執行。建置的本機 helper
`artifacts/build_fusion.py` 以任務專屬 profile/temp 呼叫原始
`scripts/build.ps1`；提供 ZIP 會同時執行 source 與 package 檢查。重跑時
請使用新的 `--output` 目錄，避免覆寫已保存的報告。

```powershell
python -B artifacts/build_fusion.py --blender "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe"
python -B scripts/run_fusion_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.1/blender.exe" --package dist/fusion/yugo-0.1.26.zip --output artifacts/fusion-final-51
python -B scripts/run_fusion_checks.py --blender "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe" --package dist/fusion/yugo-0.1.26.zip --output artifacts/fusion-final-52
python -B scripts/check_task_scope.py
python -B scripts/check_version.py
python -B scripts/check_governance.py
```

真人 GUI、實際網路設備、官方 VRM extension／私人素材相關項目，需要具體
prerequisite 與結果另行記錄。此任務未建立 release tag 或覆寫既有發布。

Floating UI probe 透過原 `yugo.register()` 建立完整候選 runtime。在獨立測試
程序暫時允許建立一個模擬舊檔的 Chat List RNA，隨即恢復退休判定，再直接
測試保留的 importer；沒有重新啟用 Chat operator 或 runtime。命令為：

```text
<Blender 5.1.2> --background --factory-startup --disable-autoexec --python-exit-code 1 --python artifacts/fusion-assets/build_floating_ui.py
<Blender 5.1.2> --background --factory-startup --disable-autoexec --python-exit-code 1 --python artifacts/fusion-assets/probe_import.py
```
