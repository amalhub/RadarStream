# RadarStream

[English](README.md) | **简体中文**

RadarStream 是一个面向 TI MIMO 毫米波雷达系列的实时原始数据采集、处理与可视化系统。

https://github.com/user-attachments/assets/7ce99b51-a1af-4025-8a84-ee580eb92d04

演示 1：实时运动检测与雷达特征可视化

## 项目概述

本系统支持德州仪器（TI）MIMO 毫米波雷达的实时原始数据采集、处理与可视化。除射频评估板外，原始数据采集还需要 DCA1000EVM。目前已完成以下评估板的测试：

- IWR6843ISK
- IWR6843ISK-OBS
- IWR1843ISK

如果在使用过程中遇到问题，欢迎提交 Issue 或 Pull Request。

## 功能特性

- **TI MIMO 毫米波雷达实时多线程数据采集：**
  - 使用多线程架构完成数据采集与处理。
  - 为绕过 Python 全局解释器锁（GIL）并实现真正的多核处理，数据采集模块使用 C 语言封装，可实现接近实时、低丢帧的数据采集与处理。
- **多维雷达特征提取：**
  - 距离-时间信息（RTI）
  - 多普勒-时间信息（DTI）
  - 距离-多普勒信息（RDI）
  - 距离-方位角信息（RAI）
  - 距离-俯仰角信息（REI）
- **交互式可视化界面**
- **雷达配置热重载：**
  - 修改合法的 Profile/Frame 参数后，再次点击“发送配置”即可应用新配置，无需重启 RadarStream。
  - 系统会根据新的 ADC/chirp/TX/RX 维度自动重建原生采集缓冲区和 DSP 处理器；雷达恢复数据流后，图像立即按照新配置更新。

## 界面预览

### 完整多维特征模式

完整特征界面使用固定的 `2 × 3` 网格显示 RTI、DTI、RDI、RAI 和 REI；雷达配置、采集和日志等 Dock 面板均可独立拖动、悬浮或重新停靠。

<p align="center">
  <img src="assets/media/new_UI2.png" width="95%" alt="RadarStream 完整多维特征界面" />
</p>

### 微多普勒模式

微多普勒模式提供更大的单特征显示区域，同时保留实时雷达配置控件。

<p align="center">
  <img src="assets/media/new_UI1.png" width="95%" alt="RadarStream 微多普勒界面" />
</p>

## 软件依赖

- Python 3.7+
- PyQt5
- PyQtGraph
- NumPy
- Matplotlib
- PySerial

## 硬件要求

- TI MIMO 毫米波雷达评估板（已测试 IWR6843ISK、IWR6843ISK-OBS 和 IWR1843ISK）
- DCA1000EVM（原始数据采集必需）
- Windows PC

## 固件要求

固件应从任意版本 `mmwave_industrial_toolbox` 内的 `mmwave_industrial_toolbox_4_10_1\labs\Out_Of_Box_Demo\prebuilt_binaries/` 对应目录选择，不强制要求使用 4.10.1 版本。

### 高帧率 RAW ADC 采集固件

仓库提供新的精简固件 [`firmware/Studio_CLI_xWR68xx_obs.bin`](firmware/Studio_CLI_xWR68xx_obs.bin)。该固件移除了片内距离/多普勒检测、CFAR、角度估计、点云等所有信号处理环节，使雷达端仅负责射频配置、原始 ADC 数据采集和 LVDS 数据传输，从而减少片内处理开销，为更高的采集帧率和更短的帧周期留出空间。雷达特征由 RadarStream 在上位机完成处理。

该固件所用 cfg 的完整格式示例如下：

```text
flushCfg
dfeDataOutputMode 1
channelCfg 15 7 0
adcCfg 2 1
adcbufCfg -1 0 1 1 1
profileCfg 0 60 20 7 40 0 0 100 1 64 2000 0 0 30
chirpCfg 0 0 0 0 0 0 0 1
chirpCfg 1 1 0 0 0 0 0 2
chirpCfg 2 2 0 0 0 0 0 4
frameCfg 0 2 64 0 40 1 0
lowPower 0 0
lvdsStreamCfg -1 0 1 0
testSrcCfg 0 0
sensorStart
```

更详细的功能范围、使用注意事项和配套配置说明请参阅 [`firmware/README_Studio_CLI_xWR68xx_obs.md`](firmware/README_Studio_CLI_xWR68xx_obs.md)。更高帧率仍会受到 chirp 时序、LVDS 带宽、DCA1000 以太网吞吐量和上位机处理能力的共同限制。

## 安装与配置

1. 克隆本仓库。
2. 安装依赖：

   ```powershell
   pip install pyqt5 pyqtgraph numpy matplotlib pyserial
   ```

3. 将毫米波雷达和 DCA1000EVM 连接至计算机。通常需要 5 V/3 A 直流电源线、网线和 Micro-USB 数据线。
4. 配置采集网卡的 IPv4 地址，可参考 mmWave Studio 使用 DCA1000EVM 时的 IPv4 配置流程。

下图展示了 Raspberry Pi 4B 和 Windows 两种采集方式。Raspberry Pi 在实时处理与显示时可处理的帧数较少，容易发生数据丢失，因此不推荐用于本项目的实时采集。

<p align="center">
  <img src="assets/media/raspberry_pi_setup.png" width="36%" alt="Raspberry Pi 采集方式" />
  <img src="assets/media/windows_setup.png" width="45%" alt="Windows 采集方式" />
  <img src="assets/media/radar_front_view.jpg" width="40.5%" alt="雷达正视图" />
  <img src="assets/media/radar_side_view.jpg" width="40.5%" alt="雷达侧视图" />
</p>

## 3D 打印固定结构

仓库提供用于安装和固定 DCA1000EVM 的 3D 打印结构 STL 文件。

**注意：**组装时还需要若干 M3 尼龙隔离柱和螺钉。

<p align="center">
  <img src="assets/media/enclosure_exploded_view.png" width="70%" alt="DCA1000EVM 固定结构爆炸图" />
</p>

## 使用方法

1. 启动主程序：

   ```powershell
   python main.py
   ```

2. 选择雷达 CLI 对应的串口。
3. 在雷达配置组件中选择“预设配置文件”或“参数生成配置”。
4. 使用预设配置时选择已有 `.cfg` 文件；使用生成模式时，通过滑动条、数字输入框以及 RX/TX 通道复选框修改 IWR6843 Profile/Frame 参数。
5. 点击“发送配置”初始化雷达；如需保留生成的配置，点击“保存配置”。
6. 使用界面实时查看雷达特征，或采集用于机器学习的数据。

程序运行期间可以继续修改参数并再次发送，无需重启 RadarStream。配置发送成功后，当前采集与可视化流水线会立即切换到新的雷达 Profile。

主界面由四个独立 Dock 面板组成：雷达数据实时显示、雷达配置、采集和日志显示。每个面板均可拖动、悬浮、重新停靠或关闭，也可以从“窗口”菜单恢复。实时系统和微多普勒是“显示模式”菜单中的互斥选项，不再使用 Tab 切换。拖动 Dock 标题栏时，界面会显示四向停靠导航和目标区域预览。

未连接雷达或 DCA1000 时也可以打开程序界面。只有点击“发送配置”后，系统才会初始化硬件和原生采集库。如果 Windows 报错 `10049`，请确认采集网卡的 IPv4 地址与 `app_config.py` 中的 `NetworkConfig.host_address` 匹配。

程序启动后会优先选择 TI XDS110 Application/User UART 或 Silicon Labs CP2105 Enhanced COM Port 作为雷达 CLI 串口。Standard/Data 端口不用于原始数据采集，因为原始采样数据通过 DCA1000 传输。

## 配置说明

运行时和硬件策略集中定义在 `app_config.py`。当前选中的 TI CLI `.cfg` 文件是 ADC 采样点数、每个 TX 的 chirp 数、TX 数量和 RX 数量的唯一配置来源。每次建立连接前，RadarStream 都会解析这些参数，按照准确的帧长度重建原生双缓冲区并重新创建 DSP 处理器。因此，在兼容的帧配置之间切换时，不再需要手动修改 `app_config.py`。

`DEFAULT_CONFIG.radar` 仅作为选择配置文件前的回退值。网络、DSP 和路径策略仍由 `AppConfig` 提供；原始帧长度、虚拟天线数量等派生值会自动计算。由于 TI CLI 指令无法可靠推断物理阵列几何，使用不同物理天线布局的 cfg 时，仍可能需要同步调整 `DspConfig` 中的方位角/俯仰角通道映射。

可停靠的雷达配置组件支持两种工作方式。`radar_configs/` 下的现有文件可以直接选择和发送，不会被重写；文件中的 `channelCfg`、`profileCfg` 和 `frameCfg` 会解析并回显到控件和性能指标中。修改任意控件后，组件会自动切换到参数生成模式。在生成模式下，RadarStream 会校验 IWR6843 频率范围、ADC 采样窗口、通道 mask 和帧时序，并将修改后的参数应用到 `radar_configs/iwr6843_micro_doppler.cfg` 模板。发送时使用自动清理的临时 `.cfg`；“保存配置”会把相同内容写入用户指定的永久文件。

### 配置热重载

修改合法参数后，点击“发送配置”即可热重载雷达配置。RadarStream 会停止当前硬件流水线、解析新的帧尺寸、重建原生采集缓冲区和 DSP 处理器、发送清理后的 CLI 指令，然后恢复实时可视化。整个过程不需要重启程序，也不需要修改 `app_config.py`。

> [!CAUTION]
> 主机端校验能够检查目前已知的频率、ADC 窗口、天线 mask 和帧时序约束，但雷达固件仍拥有最终决定权。不受支持或非法的参数组合可能在 `sensorStop` 后被固件拒绝，并且偶尔会让雷达 CLI 无法通过再次发送配置自行恢复。遇到这种情况时，请按下评估板上的物理 **RESET/NRST** 按钮，等待重新出现 `mmwDemo:/>` 提示符，然后发送一份已知可用的配置。

可变的 UI/DSP 协调状态单独保存在 `runtime_state.py` 中，旧的字符串键全局状态模块已经从应用流程中移除。

核心回归测试不依赖雷达硬件：

```powershell
python -m unittest discover -s tests -v
```

## 项目结构

- `assets/`：静态资源
  - `media/`：README 图片和演示媒体
  - `cad/`：3D 打印 STL 与 CAD 源文件
- `radar_configs/`：TI 雷达 CLI 配置文件
- `firmware/`：雷达固件二进制文件
- `native/`：受支持平台的原生 UDP 采集二进制文件
- `radar_dsp/`：可复用的底层雷达 DSP 算法
- `radar_configurator/`：可复用的 IWR6843 cfg 引擎与紧凑型 PyQt5 组件
- `tests/`：不依赖硬件的回归测试
- `main.py`：应用入口和组件装配入口
- `app_config.py`：集中式不可变应用配置
- `runtime_state.py`：线程安全的显示与采集运行状态
- `data_pipeline.py`：原生采集缓冲区和处理线程
- `signal_processor.py`：有状态的 RTI/DTI/RDI/RAI/REI 特征提取
- `hardware_interfaces.py`：雷达 EVM 和 DCA1000 通信适配器
- `radar_profile.py`：TI CLI Profile 形状校验
- `radar_tlv.py`：IWR6843 配置与 TLV 解析器
- `main_window_ui.py`：基于 Dock 的 PyQt5 主窗口布局
- `colormap_utils.py`：PyQtGraph 颜色映射转换工具

## 论文引用

如果本项目对您的研究有所帮助，请考虑引用以下与本工具密切相关的论文：

```bibtex
@ARTICLE{11270504,
  author={Chen, Qin and Lu, Qunfeng and Chen, Yaoxi and Tian, Yu and Cui, Zongyong and Cao, Zongjie},
  journal={IEEE Transactions on Instrumentation and Measurement},
  title={Domain-Generalized Gesture Recognition via mmWave Radar Signal Multi-View Learning},
  year={2025},
  doi={10.1109/TIM.2025.3637962}}

@ARTICLE{10714388,
  author={Chen, Qin and Cui, Zongyong and Tian, Yu and Chen, Yaoxi and Cao, Zongjie},
  journal={IEEE Internet of Things Journal},
  title={Joint Position Estimation for Hand Motion Using MIMO FMCW mmWave Radar},
  year={2025},
  volume={12},
  number={3},
  pages={2838-2853},
  doi={10.1109/JIOT.2024.3478234}}

@ARTICLE{10288185,
  author={Chen, Qin and Cui, Zongyong and Zhou, Zheng and Tian, Yu and Cao, Zongjie},
  journal={IEEE Internet of Things Journal},
  title={MMHTSR: In-Air Handwriting Trajectory Sensing and Reconstruction Based on mmWave Radar},
  year={2024},
  volume={11},
  number={6},
  pages={10069-10083},
  doi={10.1109/JIOT.2023.3325258}}
```

## 致谢

特别感谢 OpenAI Codex；如果没有它的协助，本项目的大规模重构将很难顺利完成。

本项目参考并使用了以下项目：

- [real-time-radar](https://github.com/AndyYu0010/real-time-radar)，作者 AndyYu0010
- [OpenRadar](https://github.com/PreSenseRadar/OpenRadar)，主要参考其 DSP 模块

## TODO

已完成：

- [x] 验证多个射频评估板的兼容性（IWR6843ISK、IWR6843ISK-OBS 和 IWR1843ISK）
- [x] 改造原生采集 API，使其能够根据选中的雷达 Profile 自动重建采集缓冲区

后续计划：

- [ ] 增加离线 RAW ADC 记录回放，以支持可重复的 DSP 分析
- [ ] 增加实时采集健康监控，包括丢包、缓冲区积压和处理延迟
- [ ] 持久化并恢复 Dock 布局、所选配置和显示偏好
- [ ] 为已支持的雷达评估板和配置 Profile 增加硬件在环自动回归测试
