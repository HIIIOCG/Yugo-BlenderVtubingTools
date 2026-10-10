# Yugo PhysX Tracker / ProtoMotions

任務 [#38](https://github.com/HIIIOCG/Yugo-BlenderVtubingTools/issues/38) 以
**Yugo PhysX Tracker 0.4.0** 取代獨立 Physics Lab 原型。程式與執行期資產位於
`addons/yugo_physx_tracker/`，測試位於 `tests/physx_tracker/`。它透過真正的
ProtoMotions SMPL AMASS learned policy 與 standalone CPU PhysX，將參考動作
轉成受物理影響的 Humanoid Motion。

## 固定匯入來源

| 項目 | 本次採用值 |
| --- | --- |
| 任務基準 | `6da97ae7ed2eb538c13ab3f9042b0f875d8e2f73` |
| 來源安裝包 | `yugo_physx_tracker-0.4.0.zip` |
| 來源大小 | 242,419,870 bytes，約 231 MiB |
| 來源 SHA-256 | `3b3b636b1a32304138030a338db4226dbd1f773748cd4af7cf1251bbc81bc48e` |
| 解壓內容 | 2,407 個檔案，609,740,832 bytes |
| Package ID | `yugo_physx_tracker`，版本 `0.4.0` |

來源是原 `motion_codedev/dist/` 已產生的固定 CPU 套件。匯入保留該 ZIP 的
產品檔案、模型、依賴與授權；沒有採用同名外部原始資料夾後續的 0.4.1 改動。
來源檔案唯讀，後續開發以 Git 任務工作區為準，不同步回外部來源。
來源 ZIP 的 checksum 用來辨識匯入材料；重建安裝包需另記自己的 checksum，
不能以此推定重建 ZIP 的二進位內容相同。此紀錄也不代表新的 GitHub Release 已發布。

原 CPU 打包已省略未使用的 PhysX GPU DLL、開發標頭、範例與快取，保留所有
上游 license / notice。原生依賴的大型二進位檔透過 **Git LFS** 管理；clone 或
切換分支後，先在工作區執行：

```powershell
git lfs pull
```

建置與原生測試需要實際 LFS 內容，只有 pointer 的 checkout 不完整。
生成的安裝 ZIP、測試報告、Blender profile 與快取不提交 repository。

## 安裝與使用

支援 **Windows x64、Blender 5.1 / 5.2**。本 repository 的 host 是
**Yugo 0.2.0**，內含 **Yugo Node 0.10.0**（`addons/yugo/node/`）。先安裝並
啟用 Yugo，再透過 Preferences → Get Extensions → Install from Disk 安裝
`yugo_physx_tracker-0.4.0.zip`，啟用 Yugo PhysX Tracker。不要同時啟用另裝的
Yugo Node 或 Yugu Node Studio：它們與 fused Yugo 共用 Node identifiers。

在 Yugo 節點編輯器建立 **PhysX Skeleton → ProtoMotions → PhysX Physics**。
Skeleton Source 選 **Built-in SMPL (No Bone Map)** 即可使用內建骨架；Model 選
**SMPL AMASS Tracker**。LIVE 模式將 Humanoid Motion 接到 Reference Motion；
試用內建片段時改選 **SMPL Walk**。按 Physics 的 **Play** 自動 Setup / Build
並開始模擬，按同一按鈕 **Pause** 暫停，**Reset** 以目前參考姿勢重設。
倒地不會自動暫停或重設。Add → PhysX Physics → Create PhysX Tracker Demo
提供已接線的 Bone Map 範例。

物理預覽自行更新；要寫回使用者角色，將 Humanoid Motion 經
**Motion to Bone Transform** 接到 **Bone Transforms to Armature**，再將
Physics 的 **Updated** 接到 Apply 的 **Enter**。Scene Rigid Bodies 與
VRM Spring Bones 只有明確接到 Physics 才加入模擬。Generate Targets 建立
11 個位置目標，Position Strength 控制有限的彈簧吸引力；它不等同舊 Physics Lab
的 Grab / Release / Attract Home 操作。

完整接線、位置目標、參考顯示、場景剛體、VRM Spring、Root Support、
模型限制與上游驗證紀錄保留在匯入的
[README.txt](../../addons/yugo_physx_tracker/README.txt)。其中提到的外部工作區
驗證是來源的歷史證據，本次 Git 候選結果另由任務 PR 記錄。

## 相容性與執行期邊界

本次移除的 Package ID 是 `yugo_physics_lab`。新套件使用
`yugo_physx_tracker`，沒有轉換舊 `Scene.yugo_physics_lab` 資料、Physics Lab demo、
抓取／回位狀態或儲存場景；舊檔保留，按新套件的接線重新建立工作流程。
Yugo 內建 MuJoCo physics、Studio Prop Physics / Ragdoll 與獨立 Ragdoll 的來源、
節點和行為維持原基準。Yugo、Studio 的 product version 與 release 路由不在此任務變更。

Tracker 保留來源的節點 ID：

| 節點 | ID |
| --- | --- |
| PhysX Skeleton | `YUGOPX_NODE_skeleton` |
| ProtoMotions | `YUGOPX_NODE_tracker` |
| PhysX Physics | `YUGOPX_NODE_physics` |
| Scene Rigid Bodies | `YUGOPX_NODE_rigid_bodies` |
| VRM Spring Bones | `YUGOPX_NODE_springs` |

Host tree 為 `YUGUS_NT_humanoid_motion`。匯入的 `bridge.py` 透過該 tree 的
RNA class 找到實際 host，包裝 `_evaluate_node`、`_action_input`、
`_Pulse._receive` 與 event socket draw；Float UI 也使用 host 的既有介面。
這些是保留的歷史私有 adapter，不是 Studio `ModuleSpec`、公開 action API 或
共用 scheduler 的新整合。相容性驗證須明確載入本 repository 的 `yugo.node`。

Addon 保留獨立 Blender timer / lifecycle handlers，每個 Physics 節點管理
自己的背景 worker。物理與 ONNX 推論在隱藏的原生 CPU 程序執行，透過
stdin / stdout 交換資料；無需 Isaac Sim、Isaac Lab、Omniverse Kit 或 PyTorch。
Load、Undo/Redo、節點移除與 unregister 停止 worker，儲存圖不自動開始模擬。
停用時撤回仍由自己持有的 bridge / UI 包裝。既有其他 Yugo 物理執行期仍各自運作。

## 建置與驗證

在本任務工作區、取得完整 LFS 內容後執行；`<exe>` 需換成 Blender 絕對路徑：

```powershell
python -B scripts/physx_build.py --blender '<exe>'
python -B scripts/physx_run_checks.py --blender '<exe>' --package dist/yugo_physx_tracker-0.4.0.zip --output artifacts/physx-checks
python -B scripts/check_task_scope.py
```

建置與檢查使用任務自己的 Blender profile、log、輸出與生成場景，核對實際
載入的 repository host / addon 及生成 ZIP，不安裝或重載使用者活躍 Blender。
需驗證 extension、來源 / ZIP、受限註冊、Play / Pause / Reset、原生 learned
policy、CPU PhysX、明確 Apply 與 worker 清理。記錄測試 commit、Blender 版本、
package checksum、實際命令及結果；缺少 prerequisite 或未執行的測試不能記為通過。
本文件列出入口與要求，實際候選驗證以 #38 PR 為準。

## 來源與授權

ProtoMotions 模型 / 觀測契約固定至
`7a8417a9f55b1586b0d6e8f46578c5bc9d7ce1f1`；`tracker.json`、`walk.json`
保留原來源與雜湊。模型為 24-body SMPL AMASS policy，1003 維 observations / 69 維
actions，未重新訓練。OVPhysX 0.6.3 / PhysX 5.11 是來源的 pre-release runtime；
CPU 移植不保證與 Isaac 的接觸、drive 或所有追蹤情境完全相同。

Addon 程式為 GPL-3.0-or-later；ProtoMotions 程式、模型、motion、SMPL 資產及
NVIDIA 原生 runtime 保留各自條款，Addon 的 GPL 宣告不重新授權這些依賴。
完整紀錄在 [NOTICE.txt](../../addons/yugo_physx_tracker/NOTICE.txt)、
`assets/licenses/`、`vendor/*dist-info/` 與 vendor 內的上游 LICENSE / notices。
