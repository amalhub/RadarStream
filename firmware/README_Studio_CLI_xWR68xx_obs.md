# Studio_CLI_xWR68xx_obs 固件说明 / Firmware Notes

## 中文

### 固件用途

`Studio_CLI_xWR68xx_obs.bin` 是面向 xWR68xx 雷达的精简采集固件。该固件移除了片上信号处理链路，将雷达端主要用于射频配置、原始 ADC 数据采集和数据传输，从而减少片上处理时间，为设置更高帧率和更短帧周期留出空间。

该固件不提供标准 OOB Demo 中的片上距离/多普勒检测、CFAR、方位角/俯仰角估计、点云生成、目标跟踪或手势识别能力。微多普勒等特征由上位机 RadarStream 处理。

### 当前配套配置

当前配套使用：[`../radar_configs/iwr6843_micro_doppler.cfg`](../radar_configs/iwr6843_micro_doppler.cfg)

该配置的主要参数为：

- 起始频率：60 GHz
- 有效扫频带宽：1 GHz
- ADC 采样点：128 samples/chirp
- 天线配置：1 Tx × 1 Rx
- 每帧 128 chirps
- Chirp rate：约 3.226 kHz（目标约 3.2 kHz）
- 帧率：25 FPS
- 帧周期：40 ms

RadarStream 的微多普勒链路使用 `TX0-RX0` 通道，并采用 128 chirps 窗口、128 chirps 步长进行逐帧、不重叠的多普勒处理。

### 使用注意事项

1. 请将该固件烧录到兼容的 xWR68xx 设备，并配合 DCA1000 等原始 ADC 数据采集硬件使用。
2. 该固件不会输出片上点云；未看到点云不表示雷达采集失败，应以上位机是否收到连续原始 ADC 数据为准。
3. 配置文件中的 ADC 点数、chirp 数、Tx/Rx 数量和帧结构必须与 RadarStream 的运行时解析结果一致，否则会造成帧长度错误或数据错位。
4. 提高帧率时仍需同时满足射频 chirp 时序、LVDS 带宽、DCA1000 以太网吞吐量和上位机处理能力限制。
5. 修改配套配置后，应重新核对每帧原始数据长度和微多普勒滑窗参数。

---

## English

### Purpose

`Studio_CLI_xWR68xx_obs.bin` is a streamlined acquisition firmware image for xWR68xx radar devices. All on-chip signal-processing stages have been removed so that the radar primarily performs RF configuration, raw ADC acquisition, and data streaming. Eliminating the on-chip processing workload leaves more timing margin for higher configured frame rates and shorter frame periods.

This firmware does not provide the on-chip range/Doppler detection, CFAR, azimuth/elevation estimation, point-cloud generation, target tracking, or gesture recognition available in the standard OOB Demo. Features such as micro-Doppler are processed by the RadarStream host application.

### Current companion configuration

Use this firmware with: [`../radar_configs/iwr6843_micro_doppler.cfg`](../radar_configs/iwr6843_micro_doppler.cfg)

The current profile uses:

- Start frequency: 60 GHz
- Effective sweep bandwidth: 1 GHz
- ADC samples: 128 samples/chirp
- Antenna configuration: 1 Tx × 1 Rx
- 128 chirps per frame
- Chirp rate: approximately 3.226 kHz (targeting about 3.2 kHz)
- Frame rate: 25 FPS
- Frame period: 40 ms

The RadarStream micro-Doppler path uses the `TX0-RX0` channel and processes each frame with a 128-chirp window and a 128-chirp hop, producing non-overlapping windows without crossing frame boundaries.

### Usage notes

1. Flash this image to a compatible xWR68xx device and use raw ADC capture hardware such as the DCA1000.
2. This firmware does not generate an on-chip point cloud. The absence of point-cloud output does not indicate capture failure; verify that the host receives a continuous raw ADC stream instead.
3. The ADC sample count, chirp count, Tx/Rx count, and frame structure in the configuration must match the shape parsed by RadarStream. A mismatch can cause incorrect frame lengths or sample alignment.
4. Higher frame rates are still limited by RF chirp timing, LVDS bandwidth, DCA1000 Ethernet throughput, and host-side processing capacity.
5. After changing the companion configuration, recalculate the raw values per frame and verify the micro-Doppler sliding-window parameters.
