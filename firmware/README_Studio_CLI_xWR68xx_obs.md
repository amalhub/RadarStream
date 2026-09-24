# Studio_CLI_xWR68xx_obs Firmware Notes

## Purpose

### Purpose

`Studio_CLI_xWR68xx_obs.bin` is a streamlined acquisition firmware image for xWR68xx radar devices. All on-chip signal-processing stages have been removed so that the radar primarily performs RF configuration, raw ADC acquisition, and data streaming. Eliminating the on-chip processing workload leaves more timing margin for higher configured frame rates and shorter frame periods.

This firmware does not provide the on-chip range/Doppler detection, CFAR, azimuth/elevation estimation, point-cloud generation, target tracking, or gesture recognition available in the standard OOB Demo. Features such as micro-Doppler are processed by the RadarStream host application.

## Current Companion Configuration

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

## Usage Notes

1. Flash this image to a compatible xWR68xx device and use raw ADC capture hardware such as the DCA1000.
2. This firmware does not generate an on-chip point cloud. The absence of point-cloud output does not indicate capture failure; verify that the host receives a continuous raw ADC stream instead.
3. The ADC sample count, chirp count, Tx/Rx count, and frame structure in the configuration must match the shape parsed by RadarStream. A mismatch can cause incorrect frame lengths or sample alignment.
4. Higher frame rates are still limited by RF chirp timing, LVDS bandwidth, DCA1000 Ethernet throughput, and host-side processing capacity.
5. After changing the companion configuration, recalculate the raw values per frame and verify the micro-Doppler sliding-window parameters.
