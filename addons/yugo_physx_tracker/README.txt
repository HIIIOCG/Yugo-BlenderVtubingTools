Yugo PhysX Tracker 0.4.0

Windows x64 / Blender 5.1–5.2，需要 Yugo Node 0.8.8 或更新版本。
套件內含 standalone PhysX、ONNX Runtime、SMPL learned tracker 與走路範例。
Blender 提供節點、骨架與動作輸入；隱藏的背景程序執行原生 PhysX 與模型推論。
不需安裝 Isaac Sim、Isaac Lab、Omniverse Kit 或 PyTorch。

使用
----
1. 安裝 ZIP extension，啟用 Yugo Node 與 Yugo PhysX Tracker。
2. 在 Yugo 節點編輯器連接：
   PhysX Skeleton → ProtoMotions → PhysX Physics。
   在 Skeleton Source 選 Built-in SMPL (No Bone Map)。
   ProtoMotions 的 Model 選 SMPL AMASS Tracker；Motion 預設為 Live Motion。
   將 Humanoid Motion 接到 Reference Motion，或切換到 SMPL Walk 播放片段。
3. 按 Physics 的 Play。第一次會自動建立骨架與背景程序，再開始模擬。
   同一個按鈕變成 Pause；再次按下可暫停，之後按 Play 繼續。
   Reset 以目前參考姿勢重新初始化；倒地本身不會自動暫停或重設。

Built-in SMPL 建立官方訓練比例的模擬/顯示骨架，不需要 Bone Map、
使用者 Armature 或額外 source armature。此模式隱藏並忽略 Bone Map 輸入。
Placement 可調整物理預覽位置；標準動作座標為 Blender 前方 -Y、左方 +X。
新版會檢查 Built-in 的座標版本、Placement 與尺寸；舊檔案保存的舊座標
binding 在下一次 Play 自動重新 Setup，修正早期版本的側向朝向。
若要使用既有骨架，在 Skeleton Source 選 Bone Map，
接 Bone Map → Skeleton。Bone Map 是預設模式，保留既有檔案的接線。
切換來源模式會停止舊背景程序；下一次 Play 自動重新建立。

Add > PhysX Physics > Create PhysX Tracker Demo 使用 Bone Map 路徑，
在目前節點圖加入新的 source armature 與四個已接線節點，並完成 Setup。
直接按 Play 即可播放附帶的 SMPL walk。自訂 clip 需符合
tracker.json 的 24-body 順序與 NPZ 契約。
Live Motion 接受 Yugo 標準 Humanoid Motion，接 ProtoMotions 的 Reference Motion。
Built-in 模式亦可接，不依賴 Skeleton 的 Bone Map。
Live Motion 保留 100 ms 緩衝；輸入停止時維持最後參考姿勢。
未接 Reference Motion 時以初始姿勢作為參考；不會自行播放 walk。
Demo 會明確選 SMPL Walk，以便直接播放附帶範例。

參考骨架與位置目標
------------------
Physics 的 Show Reference 顯示半透明的參考骨架；Reference Opacity 可調整
透明度，預設 0.2。Solid 模式也可顯示。它跟隨策略實際使用的延遲參考姿態，
與青綠色的物理結果分開；角色倒地時，參考骨架仍顯示原參考動作。

按 Generate Targets 產生 11 個可用 Blender 一般移動操作編輯的球形目標：
hips、chest、head，以及左右 palms、elbows、feet、knees。
可在 Play 之前產生，重複按不會新增重複物件或清除已編輯的位移。
目標跟著參考動作移動與旋轉，保留你調整的局部位置偏移；
暫停、Reset、重建背景程序及存檔重載也保留偏移。
Clear Targets 移除該 Physics 節點的目標，透明參考顯示仍保留。

Position Strength 是 0–2 數值輸入，可直接設定或連接 Yugo Float。
0 關閉、1 標準（預設）、2 較強；未產生目標時不施加吸引力。
Spring Frequency（預設 3 Hz）、Spring Damping（預設 1）及
Max Force / Point（預設 250 N）控制有限的彈簧阻尼吸引力。
播放或暫停中調整會直接生效，不重建策略或物理場景。
目標只讀取位置；作用點的外力仍會自然帶動身體旋轉。
沒有角度目標、自訂拖曳模式或直接搬動物理骨骼的控制。

這是參考動作追蹤之外的物理輔助，與 Root Support、碰撞和 spring 力共同
作用。SMPL AMASS 模型仍追蹤完整 Reference Motion，沒有換成學習稀疏點
補全動作的模型。過大的位移或吸引力可能使角色偏離動作或倒地；
模擬會繼續，由使用者選擇 Pause 或 Reset。

把物理動作接回角色
------------------
加入 Motion to Bone Transform（下文稱 Bind）與
Bone Transforms to Armature（下文稱 Apply）兩個 Yugo 節點。
Humanoid Motion → Bind 的 Motion；目標角色的 Bone Map → Bind。
Bind 的 Bone Transform → Apply 的 Bone Transform。
Physics 的 Updated → Apply 的 Enter，讓每一個收到的物理狀態更新角色。
Done 亦保留每幀更新，以相容既有接線。無需另外加入 Hz Trigger。
物理預覽骨架會自行更新；Apply 是寫入使用者角色的明確接線。
Humanoid Motion 的子骨骼輸出以旋轉為主，僅 hips 帶根部位移；
目標角色用自己的 rest 長度與父子關係還原位置，支援 connected 骨骼
以及與 SMPL 不同的角色比例，也可先經過 Humanoid Motion Smooth。

場景剛體
--------
Scene Rigid Bodies → Physics 的 Rigid Bodies，才會把場景碰撞物加入該模擬。
Scene Rigid Bodies 的 Objects 未接線時，讀取目前場景中啟用的 Blender 剛體。
若接 Objects List，只使用清單內的物件；已接的空清單代表不加入物件。
單獨存在讀取節點不會啟動模擬，也不會自動加入任何碰撞物。

播放或暫停中都會自動同步碰撞輸入，不必重建背景程序或再次按 Play。
可加入、刪除、啟用或停用來源剛體，改變 Objects List、接線、名稱、
質量、摩擦、碰撞形狀與縮放。來源變動約每秒檢查 4 次；
單純移動的 kinematic 邊界持續以原本頻率更新。
新剛體由來源目前求值姿態加入；既有剛體保留物理姿態與速度，
SMPL 策略、播放時間、LIVE 參考及 Root Support 不重新初始化。
暫停時也能套用變動，不推進模擬時間。
無法使用的編輯會顯示訊息並保留最後有效的物理場景；
修正來源後繼續同步，播放不會因此自動停止。

ACTIVE 剛體可在 PhysX 中自由運動；PASSIVE 與 animated ACTIVE 作為跟隨來源
目前求值姿態的 kinematic 邊界。讀取的是目前幀求值後的位置與縮放。
PhysX 使用獨立碰撞物與預覽副本，不修改原物件的位置、Blender 剛體設定
或時間軸。Blender 的碰撞群組等未支援設定會提出訊息；
動態凹面 Mesh 不支援，請使用 Convex Hull 等適合的形狀。
額外剛體、spring 節段與 anchor 合計上限 512；資料串流亦有大小限制，
大量或名稱很長的物件可能需要較少的數量。

VRM Spring Bones
----------------
VRM Data 的 Spring Data → VRM Spring Bones → Physics 的 Spring Bones。
使用原生 PhysX capsule 與 D6 關節模擬，保留來源骨架的 bone roll。
VRM 1.0 以相鄰 joint 建立節段，最後一個 joint 作為末端位置；
VRM 0.0 從 root 展開子骨骼，保留分支與末端 bone tail。
支援球形與膠囊 collider；inside 與 plane collider 會略過並顯示訊息。
這是 VRM 參數到 PhysX 的 adapter，不宣稱與 VRM 原本 Verlet solver 完全相同。

Spring Motion 是 Yugo 的 Bone Transform，可以接 Apply；
也可以作為 Bone Transform Mix 的 Overlay，Humanoid Motion 經 Bind
後接 Mix 的 Base，再將 Mix 接 Apply，並由 Physics Updated 驅動 Apply。
只有明確接 Apply 才會寫入來源骨架。
附著到 SMPL 身體的 spring 輸出是來源 rest anchor 的局部次級動作，
會跟隨同一幀的角色父骨姿態，不重複加入身體動作或 Placement 的位移。
物理預覽仍顯示原生物理座標與 SMPL 身體比例；目標 VRM 骨架比例可與之不同。

Root Support
------------
Physics 的 Root Support 是 0–2 數值輸入，可直接設定或接 Yugo Float。
0 關閉（預設），1 標準，2 較強。播放或暫停時均可調整，不必重建程序。
輔助作用在 Pelvis 的受限彈簧/阻尼力與扭矩，不是直接鎖住或搬動根部。
學習策略仍控制關節；更強的輔助可能改變接觸與追蹤結果，未必誤差更小。
無論是否開啟輔助，倒地後仍繼續模擬，由使用者選擇 Pause 或 Reset。

背景程序與範圍
--------------
預設使用 Blender 附帶的 Python 與套件 vendor 原生函式庫，正常操作不需
指定 Python 路徑。Windows 背景程序不開啟命令視窗。
物理計算在 PhysX 的原生 C++ CPU 核心，Python 負責控制與資料交換。
本交付為 CPU-only 套件，省略未使用的 GPU DLL、開發標頭、範例與說明文件，
保留所有上游授權。每個 Physics 節點管理一個 Actor，可建立多個獨立節點。
資料讀取不推進模擬。角色骨架、模型、參考模式或全域 solver、步進率、
迭代次數變更仍需重新建立；下一次 Play 會建立新的背景程序。
碰撞輸入的增刪與設定變更遵循上述即時同步流程。
載入檔案、Undo/Redo、移除節點與停用 addon 會停止背景程序。
原先其他 Yugo 物理 addons 維持各自的節點與執行程序。

模型為 ProtoMotions SMPL AMASS 的真正 learned policy，
1003 維 observations / 69 維 actions，24 個身體、23 個球形關節。
保持官方 SMPL 拓樸與物理參數，不任意將目標 VRM 的比例套到模型。
策略更新 30 Hz；物理步進率、solver 與迭代次數顯示於 Physics 節點。
OVPhysX 0.6.3 / PhysX 5.11 為上游 pre-release runtime。
這是原生 CPU PhysX 整合，不是完整 Isaac Sim 移植；相同物理核心
不保證與 Isaac 的接觸、drive 或控制行為完全相同。模型未重新訓練。

驗證結果與限制
--------------
Windows / CPU PhysX 120 Hz / TGS / 8 次 position iterations、policy 30 Hz、
Root Support 0 的基線實測：
- 官方約 5.1 秒走路片段 + 維持末姿勢，合計 60 秒：無倒地，
  平均 body position error 0.01882 m，最低根高 0.88411 m。
- 凍結第一幀參考 20 秒：無倒地，平均誤差 0.03886 m。
- Pelvis COM 60 N 橫向力持續 0.1 秒、合計 20 秒：無倒地，平均 0.02000 m。
- 官方動作以 60 fps 串流經 100 ms buffer 追蹤 20 秒：平均 0.02027 m。
- 已記錄的約 11.98 秒 VMC idle 離線重播並維持末姿勢、合計 20 秒：
  無倒地，平均 0.01290 m。這是記錄重播，不是重新連線的即時 VMC 測試。
- Blender 5.1.2 / 5.2.0 合成手臂動作 5 秒 LIVE 基線通過，平均約 0.01316 m，
  最低根高約 0.93598 m，取樣約 60.15 Hz。輸入中斷維持末姿勢；
  新參考立即 Reset 的原生位置誤差小於 4e-7 m。

0.4.0 在 Blender 5.1.2 / 5.2.0 實際背景程序驗證單次 Play / Pause、
剛體落下接觸、清單篩選、VRM 0.0 / 1.0 spring、原物件不變、
Humanoid Motion → Bind → Apply、混合 spring 動作與即時 Root Support。
另以強力擊倒原生角色驗證倒地後持續模擬，沒有自動暫停或重設。
兩個 Blender 版本各完成 16 次播放/暫停中的碰撞場景編輯與原生確認，
全程同一個背景程序，LIVE 參考與 Root Support 0.65 保留。
暫停編輯期間原生 SMPL 姿態、時間、策略次數及既有剛體姿態不變；
Box 改為 Sphere 並調整地板後，實際接觸位置隨新幾何更新。
非法的動態凹面 Mesh 編輯被拒絕時，原有效模擬持續播放。
ZIP 解壓縮背景程序與隔離設定中的實際 extension 安裝另有驗證報告。
另外驗證 connected spine/chest 的 Smooth → Bind → Apply，
Bone Map / Built-in 模式與不同骨架比例均可持續更新，沒有子骨骼位移衝突。
11 個位置目標的生成、跟隨、位移保存、數值接線、暫停更新、Reset、
重建及儲存重載均經實際 Blender／原生程序驗證。
原生 TGS / PGS 測試確認零強度不改變基線、有限外力與力臂效應、
無直接角度控制或姿態覆寫，並與 Root Support、既有物理場景共同運作。
Solid GPU 畫面測試確認 opacity 0 / 0.25 / 1 的透明顯示與繪圖狀態清理。

原先 MuJoCo 移植曾使用 600 Hz；solver 與設定不同，此差異不是效能基準。
上述結果不代表所有 AMASS、即時 VMC、骨架比例或碰撞情境已穩定。
JSON 報告與獨立測試程式位於工作區。

來源與授權
----------
模型與觀測契約取自 NVlabs/ProtoMotions commit
7a8417a9f55b1586b0d6e8f46578c5bc9d7ce1f1。
tracker.json、walk.json 保留來源與 SHA-256。見 NOTICE.txt 與 assets/licenses。
Addon 程式碼 GPL-3.0-or-later；上游程式、模型與資產維持各自條款。
OVPhysX 與 OVStage 預編譯 runtime 適用 NVIDIA 原始授權；開源 source
授權不會重新授權二進位依賴。詳見 vendor 的 LICENSE。
