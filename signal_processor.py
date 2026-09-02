"""Stateful feature extraction for RadarStream.

The legacy module exposed module-level queues, arrays and counters.  The
``RadarSignalProcessor`` instance now owns that state, so configurations and
multiple processors cannot accidentally interfere with one another.
"""

from collections import deque

import numpy as np

import radar_dsp.angle_estimation as angle_dsp
import radar_dsp.compensation as compensation
import radar_dsp.music as music
import radar_dsp.range_processing as range_processing
import radar_dsp.utils as utils
from app_config import DEFAULT_CONFIG
from radar_dsp.doppler_processing import doppler_processing
from radar_dsp.utils import Window
from runtime_state import RuntimeState


def doppler_fft(data, window_type_2d=None):
    fft_input = np.transpose(data, axes=(1, 0))
    if window_type_2d:
        fft_input = utils.windowing(fft_input, window_type_2d, axis=1)

    fft_output = np.fft.fft(fft_input, axis=1)
    fft_log_abs = np.log2(np.maximum(np.abs(fft_output), np.finfo(float).tiny))
    return np.fft.fftshift(fft_log_abs, axes=1)


class RadarSignalProcessor:
    """Convert ADC frames into RTI, DTI, RDI, RAI and REI features."""

    def __init__(self, config=DEFAULT_CONFIG, runtime_state=None):
        self.config = config
        self.radar = config.radar
        self.settings = config.dsp
        self.runtime_state = runtime_state or RuntimeState()

        history = self.settings.history_frames
        self.rti_history = deque(maxlen=history)
        self.rdi_history = deque(maxlen=history)
        self.rai_history = deque(maxlen=history)
        self.rei_history = deque(maxlen=history)
        self.micro_doppler_history = deque(
            maxlen=self.settings.micro_doppler_history_frames
        )
        self._micro_doppler_chirp_buffer = np.empty(
            (0, self.radar.adc_samples), dtype=np.complex128
        )

        angle_shape = (self.settings.angle_bins, self.settings.bins_processed)
        self.range_azimuth = np.zeros(angle_shape)
        self.range_elevation = np.zeros(angle_shape)
        _, self.steering_vector = angle_dsp.gen_steering_vec(
            self.settings.angle_range_degrees,
            self.settings.angle_resolution_degrees,
            self.settings.beamforming_antennas,
        )

        self._detected_frame_count = 0
        self._capture_delay_count = 0

    def process_time_features(
        self,
        adc_data,
        window_type_1d=None,
        clutter_removal_enabled=True,
        cfar_enabled=False,
    ):
        del cfar_enabled  # Reserved for API compatibility.
        self._validate_adc_frame(adc_data)
        adc_data = np.transpose(adc_data, (0, 2, 1))
        sample_end = min(self.settings.input_sample_limit, adc_data.shape[2])
        selected = adc_data[:, :, :sample_end:self.settings.rti_sample_step]
        radar_cube = range_processing.range_processing(
            2 * selected,
            window_type_1d,
            axis=2,
            fft_size=self.settings.range_fft_bins,
        )

        if clutter_removal_enabled:
            radar_cube = compensation.clutter_removal(radar_cube, axis=0)

        range_doppler_fft, _ = doppler_processing(
            radar_cube,
            num_tx_antennas=self.radar.tx_antennas,
            interleaved=False,
            clutter_removal_enabled=False,
            window_type_2d=Window.HANNING,
            accumulate=False,
        )

        rdi_abs = np.transpose(
            np.fft.fftshift(np.abs(range_doppler_fft), axes=2), (0, 2, 1)
        )
        self.rdi_history.append(np.flip(rdi_abs, axis=0))
        rdi_frames = np.asarray(self.rdi_history)

        distance_matrix = radar_cube[:, 0, :]
        self._update_capture_trigger(distance_matrix)

        self.rti_history.append(distance_matrix)
        rti_frames = np.asarray(self.rti_history)
        range_bins = radar_cube.shape[2]
        rti_output = np.reshape(rti_frames, (1, -1, range_bins)).transpose((1, 2, 0))
        rti_output = np.flip(np.abs(rti_output), axis=1)
        rti_output[rti_output < self.settings.rti_noise_floor] = 0

        # doppler_fft returns (range_bins, doppler_bins). Building the stack
        # from its actual output keeps the pipeline valid when chirps_per_tx
        # differs from the historically hard-coded value of 64.
        micro_doppler = np.asarray(
            [doppler_fft(frame, Window.HANNING) for frame in rti_frames]
        )
        micro_doppler_output = micro_doppler.sum(axis=1)
        micro_doppler_output[
            micro_doppler_output < self.settings.micro_doppler_noise_floor
        ] = 0

        return rti_output, rdi_frames, micro_doppler_output

    def process_micro_doppler(
        self,
        adc_data,
        window_type_1d=Window.HANNING,
        clutter_removal_enabled=True,
    ):
        """Build a long Doppler-time strip from one physical RX channel.

        This path deliberately avoids the range-Doppler and angle processing
        used by the full-feature display. The decoded virtual-channel layout puts
        the first transmitter's physical RX channels first, so selecting an RX
        index here uses exactly one antenna throughout the calculation.
        """

        self._validate_adc_frame(adc_data)
        rx_channel = self.settings.micro_doppler_rx_channel
        # Range processing is independent for every chirp, so frames can be
        # concatenated afterwards without changing the result.  Unlike the
        # full-feature path, use all ADC samples from the dedicated profile.
        selected = adc_data[:, :, rx_channel]
        range_profiles = range_processing.range_processing(
            2 * selected,
            window_type_1d,
            axis=1,
            fft_size=selected.shape[1],
        )
        self._micro_doppler_chirp_buffer = np.concatenate(
            (self._micro_doppler_chirp_buffer, range_profiles), axis=0
        )

        window_chirps = self.settings.micro_doppler_window_chirps
        hop_chirps = self.settings.micro_doppler_hop_chirps
        while self._micro_doppler_chirp_buffer.shape[0] >= window_chirps:
            window = self._micro_doppler_chirp_buffer[:window_chirps]
            if clutter_removal_enabled:
                window = compensation.clutter_removal(window, axis=0)
            # Sum linear Doppler power over range first, then convert to dB.
            # Summing per-range logarithms makes the value scale with the
            # number of ADC/range bins and saturated the previous fixed UI
            # color range when the profile changed from 64 to 128 samples.
            fft_input = np.transpose(window, axes=(1, 0))
            fft_input = utils.windowing(fft_input, Window.HANNING, axis=1)
            doppler_fft_output = np.fft.fft(fft_input, axis=1)
            doppler_power = np.sum(np.abs(doppler_fft_output) ** 2, axis=0)
            spectrum = 10 * np.log10(
                np.maximum(doppler_power, np.finfo(float).tiny)
            )
            spectrum = np.fft.fftshift(spectrum)
            spectrum[spectrum < self.settings.micro_doppler_noise_floor] = 0
            self.micro_doppler_history.append(spectrum)
            self._micro_doppler_chirp_buffer = (
                self._micro_doppler_chirp_buffer[hop_chirps:]
            )

        if not self.micro_doppler_history:
            return np.empty((0, window_chirps))
        return np.asarray(self.micro_doppler_history)

    def reset_micro_doppler_history(self):
        """Start a fresh time strip when its display mode is selected again."""

        self.micro_doppler_history.clear()
        self._micro_doppler_chirp_buffer = np.empty(
            (0, self.radar.adc_samples), dtype=np.complex128
        )

    def _update_capture_trigger(self, distance_matrix):
        if not self.runtime_state.capture_enabled:
            self._detected_frame_count = 0
            self._capture_delay_count = 0
            return

        start = self.settings.detection_range_start
        stop = min(self.settings.detection_range_stop, distance_matrix.shape[1])
        points_above_threshold = np.count_nonzero(
            distance_matrix[:, start:stop] > self.settings.detection_threshold
        )
        if points_above_threshold > self.settings.detection_min_points:
            self._detected_frame_count += 1

        if self._detected_frame_count < self.settings.detection_required_frames:
            return

        self._capture_delay_count += 1
        if self._capture_delay_count < self.settings.capture_delay_frames:
            return

        if self.runtime_state.claim_capture_interval():
            self.runtime_state.mark_capture_ready()
        self._detected_frame_count = 0
        self._capture_delay_count = 0

    def process_angle_features(
        self,
        data,
        padding_size=None,
        clutter_removal_enabled=True,
        window_type_1d=Window.HANNING,
        music_enabled=False,
    ):
        del padding_size  # Retained for callers of the original API.
        self._validate_adc_frame(data)
        adc_data = np.transpose(data, (0, 2, 1))
        sample_end = min(self.settings.input_sample_limit, adc_data.shape[2])
        selected = adc_data[:, :, :sample_end:self.settings.angle_sample_step]
        radar_cube = range_processing.range_processing(
            2 * selected,
            window_type_1d,
            axis=2,
            fft_size=self.settings.range_fft_bins,
        )

        if clutter_removal_enabled:
            radar_cube = compensation.clutter_removal(radar_cube, axis=0)

        frame_snr = np.log(np.maximum(np.sum(np.abs(radar_cube)), np.finfo(float).tiny))
        frame_snr -= self.settings.angle_snr_offset
        if abs(frame_snr) < self.settings.angle_snr_dead_zone:
            frame_snr = 0

        azimuth_channels = list(self.settings.azimuth_channels)
        elevation_channels = list(self.settings.elevation_channels)
        elevation_signs = np.asarray(self.settings.elevation_phase_signs)[:, None]
        range_bin_count = min(self.settings.bins_processed, radar_cube.shape[2])

        for index in range(range_bin_count):
            if music_enabled:
                self.range_azimuth[:, index] = music.aoa_music_1D(
                    self.steering_vector,
                    radar_cube[:, azimuth_channels, index].T,
                    num_sources=1,
                )
                self.range_elevation[:, index] = music.aoa_music_1D(
                    self.steering_vector,
                    radar_cube[:, elevation_channels, index].T,
                    num_sources=1,
                )
            else:
                self.range_azimuth[:, index], _ = angle_dsp.aoa_capon(
                    radar_cube[:, azimuth_channels, index].T,
                    self.steering_vector,
                    magnitude=True,
                )
                self.range_elevation[:, index], _ = angle_dsp.aoa_capon(
                    radar_cube[:, elevation_channels, index].T * elevation_signs,
                    self.steering_vector,
                    magnitude=True,
                )

        azimuth = np.flip(np.abs(self.range_azimuth), axis=1)
        elevation = np.flip(np.abs(self.range_elevation), axis=1)
        azimuth = np.minimum(azimuth, azimuth.max() / 2)
        elevation = np.minimum(elevation, elevation.max() / 2)
        azimuth = self._normalize_angle_map(azimuth, frame_snr)
        elevation = self._normalize_angle_map(elevation, frame_snr)

        self.rai_history.append(azimuth)
        self.rei_history.append(elevation)
        return np.asarray(self.rai_history), np.asarray(self.rei_history)

    def _validate_adc_frame(self, adc_data):
        expected_shape = (
            self.radar.chirps_per_tx,
            self.radar.adc_samples,
            self.radar.virtual_antennas,
        )
        if adc_data.shape != expected_shape:
            raise ValueError(
                "ADC frame must use (chirps, adc_samples, virtual_antennas) "
                "layout {}; received {}".format(expected_shape, adc_data.shape)
            )

    @staticmethod
    def _normalize_angle_map(angle_map, frame_snr):
        maximum = angle_map.max()
        if maximum == 0:
            return np.zeros_like(angle_map)
        return angle_map / maximum * frame_snr
