# GAPSENSE S2 标定软件 V1.1 开发文档

单文件桌面程序，替代原 Qt 版 `JJ_Calibration_Tool`，并合并「标定计算器测试版_容错版.html」的拟合功能。
V1.1 与 `标定软件exe\` 的唯一结构差异：**A~F 求解从 index.html 抽出到 `src\algorithm.py`，方案由 profile 表驱动**。

- 可执行文件：`GAPSENSE-S2-标定软件V1.1.exe`（双击运行）
- 源码目录：`src\`
- 目标系统：Windows 10 1803+ / Windows 11（需要 WebView2 运行时）

---

## 1. 功能范围

| 页签 | 功能 |
|---|---|
| 标定采集 | 压力机 TCP 连接与加压、蓝牙串口连接、A~F 参数读写、名称/G01~G03/关机、8 通道实时曲线、CSV 导出 |
| 参数计算 | 上传 5 个 CSV → 自动判左右侧 → 6 个方案拟合 → 得到 A~F → 一键填入采集页 |

与原 Qt 软件的对应关系：

| 原 Qt 功能 | 新实现 |
|---|---|
| 压力机连接 / 加压 / 停止 / 回位 / 初始化 | 采集页按钮 → `Api.tcp_command` |
| 串口连接 / 写参数 / 验证 / 关机 | 采集页按钮 → `Api.serial_write_params` / `serial_command` |
| 第 5 帧取零点、左右压力换算 | `SerialLink._handle` |
| 导出 CSV（8 通道全非零才记录） | `CsvLogger` |
| 获取参数 | 从参数计算页带入当前传感器、当前方案的 A~F（不再读 CSV） |
| 拟合验证 | 采集页「拟合验证」导入写入后采集的 CSV，按 10N 分段统计 J/K 误差 |
| 获取补偿参数 E/F | 已删除，E/F 随方案一起带入 |
| 原软件内置的 5 个传感器示例 | 已全部删除，改为上传 CSV |

原 HTML 里的 8 个内置样例传感器也已清空（`SENSORS = []`），只保留上传后拟合。

---

## 2. 技术栈

| 层 | 组件 | 说明 |
|---|---|---|
| 窗口/渲染 | pywebview 6.2.1 + Edge WebView2 | Chromium 内核，`gui="edgechromium"` |
| 后端 | Python 3.11.7 | 串口、TCP、CSV、文件对话框、状态缓存 |
| 算法 | numpy 2.4.6 | 6 个方案的 A~F 求解（`src\algorithm.py`） |
| 串口 | pyserial 3.5 | 115200 8N1 |
| 前端 | 原生 HTML/CSS/JS | 无框架、无构建步骤，图表用 Canvas 2D |
| 打包 | PyInstaller 6.22.3 | onefile / windowed |

Python 负责 I/O 和**参数求解**；前端只做展示：读 CSV、画图、分段 RMS、容差校验、推荐标记。

---

## 3. 目录结构

```
标定软件V1.1\
├─ GAPSENSE-S2-标定软件V1.1.exe 交付程序
├─ build.spec                   打包配置（SRC 指向本目录 src）
├─ tests\
│  ├─ golden.json               原 JS 求解器产出的基准参数
│  └─ test_algorithm.py         回归测试
└─ src\
   ├─ main.py                   窗口入口
   ├─ backend.py                协议 + 串口/TCP/CSV + js_api（含 fit_all / get_k）
   ├─ algorithm.py              **唯一的 A~F 求解实现**（profile 表驱动）
   └─ web\
      └─ index.html             界面 + 绘图 + 分段 RMS（不含求解器）
```

`APP_DIR` 规则（`backend.py:16`）：

- 打包后 = exe 所在目录
- 源码运行 = `标定软件V1.1\`（`backend.py` 的上一级）

所以两种方式下 CSV 都落在 `标定软件V1.1\CSV_file\`。

---

## 4. 开发环境

本次开发用的临时环境（可能被系统清理，建议复制到项目内）：

```
C:\Users\WP\AppData\Local\Temp\gapsense-venv
```

依赖版本固定为：

```
pywebview==6.2.1
pyserial==3.5
pyinstaller==6.22.3
numpy==2.4.6
```

重建环境：

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install pywebview==6.2.1 pyserial==3.5 pyinstaller==6.22.3 numpy==2.4.6
```

源码直接运行（改前端或后端后不必打包）：

```bat
"C:\Users\WP\AppData\Local\Temp\gapsense-venv\Scripts\python.exe" "E:\EMB-01\S2标定计算器\标定软件V1.1\src\main.py"
```

跑算法回归测试：

```bat
"C:\Users\WP\AppData\Local\Temp\gapsense-venv\Scripts\python.exe" -m unittest discover -s "E:\EMB-01\S2标定计算器\标定软件V1.1\tests" -v
```

调试窗口：把 `src\main.py:28` 的 `debug=False` 改成 `True`，可右键打开 DevTools、查看 JS 报错。

注意：打包后 `index.html` 在 `_MEIPASS` 临时目录里，改前端必须重新打包才生效；源码运行则直接刷新。

---

## 5. 算法分层（V1.1）

求解流程：

```
index.html  读 5 个 CSV → 自动判左右侧 → 剔除无信号文件
            ↓  Api.fit_all(files, center_idx)      files = [[压力,4通道], ...]
algorithm.py  fit_all() → {'S1': [A..F], 'S2联合': [...], ...}
            ↓
index.html  用返回的 A~F 算分段 RMS、画图、推荐
```

### 5.1 方案配置表

6 个方案集中在 `algorithm.py` 的 `PROFILES`（`algorithm.py:27`）：

| 字段 | 取值 | 含义 |
|---|---|---|
| `family` | `alt` / `opt` | alt=原始通道值、边界按 k 均值缩放；opt=逐通道归一化、绝对边界 20~200 |
| `weight` | `eq` / `tol10` / `tol` | 等权 / 容差权（alt 版）/ 容差权（opt 版） |
| `center` | 1 / 8 / 16 | 中心文件权重倍数 |
| `high` | `none` / `fixed4` / `linear4` | 150N 以上权重：不加 / 固定 ×4 / 150→220N 线性 1→4 |
| `bounds` | (0.6,1.4) 等 | alt 专属：A~D 边界 = 倍数 × k 均值 |

**例：把「S2中心8x」改成 10x**，只改一行：

```python
"S2中心8x": dict(family="alt", weight="eq", center=10, high="none", bounds=(0.6, 1.4)),
```

同时把 `SCHEME_ORDER` 和前端 `SCHEMES` / `REC_WEIGHT` 里的显示名改成 `S2中心10x`（不改名也能跑，只是名字会误导）。

新增方案：在 `PROFILES` 加一项、`SCHEME_ORDER` 加名字，前端 `SCHEMES`（`index.html:555`）和 `REC_WEIGHT`（`index.html:759`）各加一项，可选补 `DOCS`（`index.html:864`）的说明。不需要写求解代码。

### 5.2 数值对齐

`algorithm.py` 是按原 JS 求解器逐行移植的（同一套交替迭代、同样的收敛阈值与边界裁剪），
在 `测试数据\` 前 5 个 CSV 上与 JS 结果的最大相对偏差 **2.4e-13**，耗时约 **0.2s**。
`tests\test_algorithm.py` 用 `tests\golden.json` 做回归：

```bat
"C:\Users\WP\AppData\Local\Temp\gapsense-venv\Scripts\python.exe" -m unittest discover -s "E:\EMB-01\S2标定计算器\标定软件V1.1\tests" -v
```

---

## 6. 打包

### 6.1 用本目录的 spec

`build.spec` 与本目录同级，`SRC` 指向 `标定软件V1.1\src`（换机器只改这一行）：

```bat
"C:\Users\WP\AppData\Local\Temp\gapsense-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ^
  --workpath "C:\Users\WP\AppData\Local\Temp\s2v11\build" --distpath "C:\Users\WP\AppData\Local\Temp\s2v11\dist" ^
  "E:\EMB-01\S2标定计算器\标定软件V1.1\build.spec"
```

产物 `GAPSENSE-S2-标定软件V1.1.exe`，复制回 `标定软件V1.1\`。
numpy 由 PyInstaller 自动收集，不走 spec 手动配置。

### 6.2 不进 spec 的直接命令

```bat
cd /d "E:\EMB-01\S2标定计算器\标定软件V1.1\src"
"C:\Users\WP\AppData\Local\Temp\gapsense-venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "GAPSENSE-S2-标定软件V1.1" --add-data "web;web" --collect-all webview --hidden-import clr main.py
```

### 6.3 打包注意

1. `web` 目录必须通过 `--add-data "web;web"` 进包，`main.py` 用 `_MEIPASS/web/index.html` 读取。
2. `--hidden-import clr` 和 `--collect-all webview` 不能省，否则 WebView2 后端可能缺文件。
3. 先关掉正在运行的程序再覆盖 exe。
4. onefile 每次启动会解压到 Temp，首次启动约 2.6 秒；要更快可改 `--onedir`。

---

## 7. 采集页操作流程

1. 填压力机 IP（默认 `192.168.1.200`）→「连接压力机」。实际连的是 **8 端口**，不是界面上的 8080（原软件同样忽略 8080）。
2. 选串口 →「连接蓝牙」。
3. 选左/右侧（左侧写参数码 1/读码 2；右侧 5/6）。
4. 「初始化参数」→「测试」→ 依次 50/100/150/200/250N。
5. 「导出数据」开始记录，100ms 一行；「结束导出」停止；「打开文件夹」定位 CSV。
6. 参数流程：在「参数计算」页拟合出 A~F → 采集页「获取参数」带入 →「写参数」下发 →「验证参数」回读 →「拟合验证」导入写入后采集的 CSV，按 10N 分段看误差。

`0N` 按钮不下发任何指令（与原软件一致，原代码该分支只有注释）。

---

## 8. 串口协议（GAPSENSE 传感器）

### 8.1 连接参数

| 项 | 值 | 依据 |
|---|---|---|
| 波特率 | 115200 | `serialport.cpp:139` |
| 数据位 | 8 | `serialport.cpp:140` |
| 停止位 | 1 | `serialport.cpp:141` |
| 校验 | 无 | `serialport.cpp:142` |
| 流控 | 无 | `serialport.cpp:143` |

### 8.2 下行指令（主机 → 传感器）

所有帧以 `B6 0D` 结尾（`0D` 即 `\r`，原软件由 `serialportwrite` 追加，本程序在 `backend.py:460-484` 直接拼入）。

| 操作 | 字节 |
|---|---|
| 写左侧参数 | `A5 5A 01 01` + ASCII `"A B C D E F"` + `B6 0D` |
| 写右侧参数 | `A5 5A 01 05` + ASCII `"A B C D E F"` + `B6 0D` |
| 读左侧参数 | `A5 5A 01 02 B6 0D` |
| 读右侧参数 | `A5 5A 01 06 B6 0D` |
| 关机 | `A5 5A 01 07 B6 0D` |
| 读名称 | `A5 5A 01 09 B6 0D` |
| G01 / G02 / G03 | `A5 5A 01 08` + ASCII `"G01"/"G02"/"G03"` + `B6 0D` |

参数为十进制文本，空格分隔，例如：

```text
A5 5A 01 01 30 2E 30 32 36 38 ... B6 0D
                    （ASCII "0.0268 0.0163 0.1263 0.0631 -0.00342 0.3223"）
```

### 8.3 上行帧（传感器 → 主机）

**数据帧，固定 26 字节：**

```
[0..3]  包号（4 字节，任意递增）
[4]     0x51
[5]     0xA0
[6..21] 8 个通道，uint16 大端，每通道 2 字节
[22]    XOR 校验（解析器不校验，仅保留）
[23]    0xB5  帧尾标记
[24..25] 温度（uint16）
```

**参数帧：**

```
00 00 00 00 + len[4] + 0xB0 + len 字节 ASCII（"A B C D E F"）
```

**名称帧：**

```
00 00 00 00 + len[4] + 0xC0 + len 字节 ASCII
```

解析实现：`SerialFrameParser`（`backend.py:65-112`）。只以 `0x51 0xA0` 和 `00 00 00 00 + B0/C0` 定位帧头、以 `0xB5` 校验数据帧尾，遇到坏帧丢弃 2 字节重同步；缓冲超过 4096 字节时只保留尾部 64 字节。

### 8.4 零点与压力换算

- 上电/连接后第 **5** 帧作为零点（`backend.py:214`），之后每帧减零点。
- 左侧用通道 0~3，右侧用通道 4~7。
- 公式（`backend.py:302-303`，与前端 `model` 一致）：

```
lin = A*(c0-z0) + B*(c1-z1) + C*(c2-z2) + D*(c3-z3)
P   = lin - (E*lin² + F*lin)
```

---

## 9. 压力机 TCP 协议

### 9.1 连接与心跳

| 项 | 值 | 依据 |
|---|---|---|
| 端口 | 8 | `tcpserver.cpp:30`（界面传 8080，被 `Q_UNUSED(port)` 忽略） |
| 心跳 | 单字节 `0x65`，每 500ms | `mainwindow.cpp:34,39` |
| 压力解码 | `(b[4] + b[5]/100 + b[6]/10000) * 9.79` | `maingui_layout.cpp:739-747` |
| 数据长度 | 至少 7 字节才解码 | `backend.py:300` |

### 9.2 指令表

| 操作 | 帧 | 发送方式 |
|---|---|---|
| 测试 | `b0a503ff41aa` | 连发 2 次，间隔 100ms |
| 停止 | `b0a500bf40aa` | 1 次 |
| 停止并回位 | `b0a504be83aa` | 1 次 |
| 关闭压力机 | `b0a504be83aa` → `b0a500bf40aa` → `b0a5323e95aa` | 每帧间隔 50ms |
| 初始化 | 下表 10 帧 | 每帧 100ms |
| 加压 | 下表 5 档 × 2 帧，每帧各发 1 次 | 每帧 100ms |

### 9.3 初始化帧（原样照抄 `maingui_layout.cpp:383-430`）

```text
b1a5000001000000000032000000000a00000000050000000000000000000000000000000000db6faa
b2a50100000000000a000000000001016100000000000100000000000000006000008beaaa
b3a5010300000001016104000000010000000000000000000000000a0100006200005a09aa
e1a50000636363000000010000000000000000000000000000000000000000000000b0bbaa
e2a50000636363000000010000000000000000000000000000000000000000000000b0bbaa
e0a514000000ffffffffff0000000000000000000000ffffffffff0000000001000a3ba9aa
f5a5ffffffffff00000000000000000000000200000000050000000000000000000000000000cd3aaa
f7a50000001501000000045d00000000000a0000001e000000003200000000000a0000001e000000003200000000000a0000001e000000003200000000000a000000000a000000000a00593eaa
b8a50000001e000000000500010000000000000000000000000000000000000000007e0eaa
b9a50000000019000002020200000000320000000000000000000000000a00000000000000000000050001000000000a000000010101000014000001000000000000050000000001000004dff2aa
```

对应 `backend.py:24-35` 的 `TCP_INIT`。

### 9.4 加压帧

| 档位 | 帧 1 | 帧 2 |
|---|---|---|
| 50N | `b2a5010000000000140000000000050955...9e31aa` | `b3a501030000000509550400000001...c427aa` |
| 100N | `b2a50100000000001400000000000a1347...30a8aa` | `b3a501030000000a13470400000001...9624aa` |
| 150N | `b2a50100000000001400000000000f1d39...ee24aa` | `b3a501030000000f1d390400000001...b725aa` |
| 200N | `b2a501000000000014000000000014272b...f86daa` | `b3a5010300000014272b0400000001...cebdaa` |
| 250N | `b2a50100000000000a0000000000182f1f...51ddaa` | `b3a50103000000182f1f00000000181a5c...b2a0aa` |

完整 16 进制见 `backend.py:37-58` 的 `TCP_PRESSURE`。原代码注释标注 250N 档"实际 20N"，按原样保留。

发送顺序：每档 `帧1, 帧2` 各发 1 次，由 `_send_sequence`（`backend.py:527`）在后台线程按 100ms 间隔发送，不阻塞界面。

---

## 10. CSV 记录格式

- 位置：`APP_DIR\CSV_file\JJ-DbToExcelyyyyMMddHHmmss.csv`
- 周期：100ms
- 门控：8 个通道必须全部非零，否则跳过该行
- 编码：UTF-8，行尾 `\r\n`
- 行格式：

```text
压力,ch0,ch1,ch2,ch3,ch4,ch5,ch6,ch7,左侧,右侧
```

- 压力保留 6 位有效数字；左侧/右侧取整
- 列索引：0=压力，1~8=通道，9=左侧，10=右侧
- 前端解析要求至少 6 列且全为数字（`index.html:986`）
- 左侧拟合用列 1~4，右侧拟合用列 5~8；E/F 补偿用列 9（左）或 10（右）

实现：`CsvLogger`（`backend.py:316-371`）。

---

## 11. js_api 接口

前端通过 `window.pywebview.api.<name>(...)` 调用，统一用 `callApi()` 包了一层（`index.html:298`）。状态 100ms 轮询一次 `get_state`（`index.html:354`）。

| 方法 | 参数 | 返回 |
|---|---|---|
| `get_state` | — | 连接状态、压力、8 通道、左右压力、A~F、名称、日志状态、`serial_rx` 计数 |
| `list_ports` | — | `[{device, description}]` |
| `connect_serial` | `port` | `{ok, error?}` |
| `disconnect_serial` | — | `{ok}` |
| `connect_tcp` | `ip` | `{ok, error?}`（端口固定 8） |
| `disconnect_tcp` | — | `{ok}`，会先发关闭序列 |
| `set_side` | `1` 或 `5` | `{ok}` |
| `serial_write_params` | `side, [A..F]` | `{ok, error?}` |
| `serial_command` | `kind, arg` | `kind`: `read` / `shutdown` / `name` / `g`（arg 为 G01~G03） |
| `tcp_command` | `kind, arg` | `kind`: `test` / `stop` / `stop_home` / `init` / `pressure`（arg 为 50~250） |
| `start_log` | — | `{ok, path}` |
| `stop_log` | — | `{ok}` |
| `open_log_folder` | — | `{ok}` |
| `pick_files` | — | 选择的 CSV 路径数组（原生对话框，多选） |
| `read_text` | `path` | `{ok, text}`，依次尝试 UTF-8-SIG / GBK / Latin-1 |
| `fit_all` | `files, center_idx` | `{ok, schemes}`；`schemes` 为 `{方案名: [A,B,C,D,E,F]}`，实现在 `src\algorithm.py` |
| `get_k` | `rows, clum` | `{ok, value}`；4 通道求和后过原点斜率（原「获取参数」用，界面已不再调用） |
| `shutdown` | — | 无返回值，关闭串口/TCP/日志 |
| `on_closing` | — | 窗口关闭事件，返回 `True` |

**硬约束**：`Api` 的实例属性必须以下划线开头（`_st/_serial/_tcp/_logger/_window/_seq_lock/_seq_cancel/_io_lock`）。pywebview 会递归内省 `dir(js_api)`，公开属性会一路钻进 WinForms 原生对象，导致递归爆栈、窗口不出现。新增属性务必用 `_` 前缀，新增接口用公开方法。

---

## 12. 前端结构与拟合

### 12.1 页面骨架

- 页签：`index.html:146`
- 采集页：`index.html:148`
- 计算页：`index.html:226`
- 采集页状态刷新：`index.html:354`
- 设备按钮绑定：`index.html:395-495`
- 计算页渲染：`index.html:628`

### 12.2 拟合流程

1. 必须选 **5 个 CSV**（1 中心 + 4 偏心），否则「开始拟合并添加」不可用（`index.html:1067`）。
2. 选择左侧或右侧；左侧取列 1~4（B~E），右侧取列 5~8（F~I）。
3. 无信号判定：某方向 4 通道幅度都 `< SPAN_MIN = 20 counts`（`index.html:1025`）即视为无信号。
4. 当前方向信号微弱、另一方向明显更强时自动切换方向（`index.html:1061-1063`）。
5. 勾选「自动剔除无信号文件」时，剔除后至少保留 4 个文件才继续拟合（`index.html:1126-1143`）。
6. 中心文件默认最后一个有效文件，可在文件列表里改选。
7. 前端把每个文件压缩成 `[压力, ch1..ch4]` 五列后调用 `Api.fit_all`（`index.html:1155`），结果写入 `DATA["自定义N"]`；`_side` 记录方向，`_raw` 保存有效段原始数据；新传感器加入 `SENSORS` 与 `customSet`。

### 12.3 模型与主要函数

模型（前端 `index.html:1018`，Python `algorithm.py` 内同名公式）：

```
t = A*c1 + B*c2 + C*c3 + D*c4
P = t - E*t² - F*t
```

**求解全部在 `src\algorithm.py`**，前端只保留数据解析与误差展示：

| 函数 | 位置 | 作用 |
|---|---|---|
| `SCHEME_ORDER` | `algorithm.py:14` | 方案顺序（前端页签按此排列） |
| `PROFILES` | `algorithm.py:27` | 每个方案的权重/中心倍数/高压权重/边界配置表 |
| `_get_range` | `algorithm.py:38` | 有效压力段：连续 3 个 >0 开始，到 >220.5 结束 |
| `_channels` | `algorithm.py:51` | 取 4 通道并按首行去零点 |
| `k_one` / `k_one_rows` | `algorithm.py:59/70` | 4 通道求和后过原点斜率，供 S1 与「获取参数」 |
| `fit_s1` | `algorithm.py:74` | 前 4 个文件各取一个 k 作为 A~D，中心文件回归 E/F |
| `_weights` | `algorithm.py:95` | 按 profile 生成容差权重、高压权重、中心倍率 |
| `_initial_ef` | `algorithm.py:130` | 迭代/优化初值（E/F 或 b=55） |
| `fit_profile` | `algorithm.py:154` | 交替迭代或优化求解，返回 `[A,B,C,D,E,F]` |
| `fit_all` | `algorithm.py:208` | 依次跑 6 个方案，返回 `{方案名: 参数}` |
| `read_csv` | `algorithm.py:216` | 读 CSV，空字段按 0 处理（对齐 JS `Number('')===0`） |

前端保留：

| 函数 | 行号 | 作用 |
|---|---|---|
| `parseCSV` | 986 | 解析 CSV，过滤非数字行 |
| `getRange` | 997 | 有效压力段（与 Python 同规则） |
| `getChannels` | 1007 | 取 4 通道并按首行去零点 |
| `model` | 1018 | 用 A~F 计算压力，画图与分段 RMS 用 |
| `runCustomFit` | 1126 | 选文件、判方向、调 `fit_all`、写 `DATA` |

方案清单（`algorithm.py:14` 与 `PROFILES:27`；前端展示 `index.html:555`）：

| 方案 | `PROFILES` 配置 |
|---|---|
| S1 | 独立实现：`fit_s1` |
| S2联合 | `family="alt", weight="eq", center=1, bounds=(0.6,1.4)` |
| S2中心8x | `family="alt", weight="eq", center=8` |
| S2容差优先 | `family="alt", weight="tol10", center=8` |
| S2中心16x+高压4x（线性版） | `family="opt", weight="tol", center=16, high="linear4"` |
| S2中心8x+高压4x（线性版） | `family="opt", weight="tol", center=8, high="linear4"` |

拟合结果元素顺序固定为 `[A, B, C, D, E, F]`，写入设备页时按此顺序填 `fwA~fwF`（`index.html:978`）。

### 12.4 设备页的「获取参数 / 拟合验证」

- 获取参数：从参数计算页取当前传感器、当前方案的 A~F（`getCurParams`），经 `deviceApplyParams` 填入 `fwA~fwF`，并把「方向」切到该传感器所属侧。
- 拟合验证：导入「写参数」并「验证参数」之后采集的 CSV；按真实压力（第 1 列）每 10N 分段，统计 J（第 10 列，左侧）或 K（第 11 列，右侧）与真实压力的平均误差、最大绝对误差。
- 若所选侧整列为 0，提示「不是写入 A~F 后采集的文件，或还没验证参数读回系数」，不出表。
- 设备页已删除 5 个 CSV 选择槽位与「获取补偿参数」按钮；A~F 统一由参数计算页产生。`calibrationPar` 保留在 `index.html` 但已无调用。

---

## 13. 常见修改位置

| 需求 | 文件:行号 |
|---|---|
| 窗口标题 / 尺寸 / 最小尺寸 | `src\main.py:18-25` |
| 开 DevTools | `src\main.py:28` |
| 串口波特率 | `src\backend.py:148` |
| 压力机 IP 默认值 | `src\web\index.html:153` |
| TCP 端口 | `src\backend.py:247`（`open(ip, port=8)`） |
| 测试/停止/回位帧 | `src\backend.py:19-21` |
| 初始化帧序列 | `src\backend.py:24-35` |
| 加压档位与帧 | `src\backend.py:37-58` |
| 第几帧取零点 | `src\backend.py:214` |
| 串口帧解析规则 | `src\backend.py:65-112` |
| CSV 周期 / 门控 / 格式 | `src\backend.py:353-371` |
| 状态轮询周期 | `src\web\index.html:354` |
| 无信号阈值 | `src\web\index.html:1025` |
| 方案顺序 / 配置 | `src\algorithm.py:14`（`SCHEME_ORDER`）与 `27`（`PROFILES`） |
| 拟合算法 | `src\algorithm.py:38-213`（`fit_profile` 在 `154`，`fit_all` 在 `208`） |
| 新增后端接口 | `src\backend.py:413-431`（`list_ports/fit_all/get_k`），前端用 `callApi` 调用 |
| 方案页签名字 | `src\web\index.html:555`；推荐权重 `759` |
| 页面布局 / 样式 | `src\web\index.html:1-290` |

新增加压档位需要同时改两处：`backend.py` 的 `TCP_PRESSURE` 加键值，`index.html` 加压按钮区（`index.html:162-167`）加 `data-press` 按钮。

---

## 14. 已知限制与排查

| 现象 | 原因 / 处理 |
|---|---|
| 双击 exe 没反应/窗口不出现 | 检查 WebView2 运行时是否安装；本机之前踩过 `Api` 属性未加下划线导致递归爆栈，新增属性务必 `_` 开头 |
| 界面空白 | `web` 目录没打进包；检查 `--add-data "web;web"` |
| 启动慢（约 2.6s） | onefile 解压开销，正常；需要更快改 onedir |
| 老机器/工控机运行失败 | 多为缺 WebView2 Evergreen Runtime，需离线安装包 |
| 串口收不到数据 | 确认蓝牙已配对、端口未被其他程序占用；看 `devMsg` 的 `last_msg` |
| 压力显示 0 | TCP 未连接，或返回帧不足 7 字节 |
| 写参数后读不回 | 侧向选错（左 1/2，右 5/6），先确认设备页方向 |
| 拟合报「该方向数据无效」 | 该侧 4 通道无信号，换另一侧或重测；阈值见 `SPAN_MIN` |
| 中心文件被自动改选 | 原中心文件在当前方向无信号，程序会选最后一个有效文件 |
| 杀软误报 | PyInstaller 单文件常见，可加白名单或改用 onedir |

内存占用比 Qt 版高（WebView2 会拉多个 `msedgewebview2.exe` 子进程），低配机器需实测。

---

## 15. 协议溯源（只读核对，未改原工程）

| 内容 | 原文件:行号 |
|---|---|
| 串口 115200 8N1 | `serialport\serialport.cpp:139-143` |
| 参数/验证/关机/名称/G0x 帧 | `gui\maingui_layout.cpp:545-645` |
| 第 5 帧零点、左右 E/F | `gui\maingui_layout.cpp:765-899` |
| TCP 连接端口 8 | `TCP\tcpserver.cpp:26-30` |
| 心跳 0x65 / 500ms | `mainwindow.cpp:34,39` |
| 关闭序列 | `mainwindow.cpp:53-60` |
| 测试帧 / 停止帧 / 回位帧 | `gui\maingui_layout.cpp:308,365,379-389` |
| 初始化帧 | `gui\maingui_layout.cpp:383-430` |
| 加压帧与发送次数 | `gui\maingui_layout.cpp:37-60,650-705` |
| 压力解码 ×9.79 | `gui\maingui_layout.cpp:737-748` |
| 26 字节数据帧构建 | 固件 `gap_nrf52832_prj\...\user_adc.c:110-128` |
| 参数帧 / 名称帧 | 固件 v1.0 `main.c:507-513,623-629` |

**实测进展（2026-11-09 真机）**：串口已连通并采到数据，压力机 TCP 加压实测 0→239.6N，`init`/`test`/`stop` 已实际下发；2026-11-09 16:03 的 CSV 左侧有效、右侧无信号，J/K 参数需先读取。仍未实测：

1. 写参数后回读（flash 写入/回读一致性），以及关机、读名称、G01~G03。
2. 初始化 10 帧在真机上的完整效果与发送节奏（原代码阻塞等待，本程序线程顺序发送）。
3. 加压帧索引与机型档位是否一致（尤其 250N 档标注"实际 20N"）。
4. 右侧传感器与长时间连续导出（数小时）稳定性。

---

## 16. 验证记录

- 串口解析单测：17/17 通过（垃圾前缀、分片、连续帧、参数帧、名称帧）。
- 写入字节/顺序单测：14/14 通过（写参数、读参数、关机、名称、G01~G03、测试两次、加压帧序）。
- 端到端：加载 5 个真实 CSV，自动判左，8 个方案全部完成（<5s）；参数 `A=0.02683676 B=0.01632999 C=0.12626906 D=0.06310992 E=-0.00342062 F=0.32230626`，与浏览器版逐位一致，中心 RMS 1.16 N。
- 打包：onefile 构建成功，启动 2.6s 出窗口，关闭无残留进程。
- **V1.1 算法迁移对齐**（`tests\test_algorithm.py`，4/4 通过，0.2s）：用原 `index.html` 的 JS 实现生成 golden（`tests\golden.json`），8 个方案逐参数比对，最大相对偏差 **2.44e-13**；同一批数据 `fit_all` 耗时约 0.2s。
- V1.1 前端接线：在浏览器中 mock `fit_all` 返回值，8 个方案页签 + 自定义均正常渲染、参数自动填入设备页。
- V1.1 打包：onefile 24.8 MB（含 numpy），启动出窗口正常。

---

## 附录：build.spec

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

SRC = r"E:\EMB-01\S2标定计算器\标定软件V1.1\src"
# 移动项目后改这一行

datas = [(SRC + r"\web", "web")]
binaries = []
hiddenimports = ["clr"]
ret = collect_all("webview")
datas += ret[0]
binaries += ret[1]
hiddenimports += ret[2]

a = Analysis(
    [SRC + r"\main.py"],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="GAPSENSE-S2-标定软件V1.1",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
```
