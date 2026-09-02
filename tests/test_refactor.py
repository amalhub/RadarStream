import importlib.util
import os
import tempfile
import unittest
from ctypes import c_int
from multiprocessing import RawArray
from pathlib import Path
from queue import Queue
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app_config import AppConfig, RadarFrameConfig
from signal_processor import RadarSignalProcessor
from data_pipeline import DataProcessor, MicroDopplerFrame, UdpListener
from hardware_interfaces import (
    Dca1000Controller,
    HardwareConnectionError,
    RadarCliClient,
    RadarCliCommandError,
    classify_cli_response,
    is_radar_config_comment,
    select_preferred_cli_port,
)
from radar_dsp import utils
from radar_dsp.angle_estimation import gen_steering_vec, peak_search
from radar_dsp.utils import DOPPLER_IDX_TO_SIGNED, Window
from radar_dsp.zoom_fft import ZoomFFT
from radar_profile import RadarProfileShape, parse_radar_profile_shape
from radar_tlv import Iwr6843TlvParser
from radar_configurator import ConfigValidationError, Iwr6843ConfigEngine
from runtime_state import FeatureMode, RuntimeState


UI_DEPENDENCIES_AVAILABLE = all(
    importlib.util.find_spec(module_name) is not None
    for module_name in ("PyQt5", "pyqtgraph", "serial")
)
if UI_DEPENDENCIES_AVAILABLE:
    from PyQt5 import QtCore, QtGui, QtWidgets


class StubCaptureBuffer:
    active_half = 0


class ProcessStubCaptureBuffer:
    def __init__(self):
        self.value = RawArray(c_int, 1)

    def capture(self):
        self.value[0] = 7


class WinErrorSocket:
    """Socket double that reproduces Windows bind error 10049."""

    def __init__(self):
        self.closed = False
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, address):
        error = OSError("requested address is not valid in its context")
        error.winerror = 10049
        raise error

    def close(self):
        self.closed = True


class FakeSerialPort:
    def __init__(self, responses):
        self.responses = list(responses)
        self.current_response = b""
        self.writes = []
        self.is_open = True

    def reset_input_buffer(self):
        self.current_response = b""

    def write(self, data):
        self.writes.append(data)
        self.current_response = self.responses.pop(0)

    @property
    def in_waiting(self):
        return len(self.current_response)

    def read(self, size):
        data = self.current_response[:size]
        self.current_response = self.current_response[size:]
        return data

    def close(self):
        self.is_open = False


class RuntimeStateTests(unittest.TestCase):
    def test_capture_events_are_consumed_once(self):
        state = RuntimeState()

        self.assertFalse(state.capture_enabled)
        state.set_capture_enabled(True)
        self.assertTrue(state.capture_enabled)

        state.open_capture_interval()
        self.assertTrue(state.claim_capture_interval())
        self.assertFalse(state.claim_capture_interval())

        state.mark_capture_ready()
        self.assertTrue(state.consume_capture())
        self.assertFalse(state.consume_capture())

        state.set_capture_enabled(False)
        self.assertFalse(state.capture_enabled)
        self.assertFalse(state.consume_capture())

    def test_feature_mode_defaults_to_full_and_can_switch(self):
        state = RuntimeState()

        self.assertIs(FeatureMode.FULL, state.feature_mode)
        state.set_feature_mode(FeatureMode.MICRO_DOPPLER)
        self.assertIs(FeatureMode.MICRO_DOPPLER, state.feature_mode)


class ConfigurationTests(unittest.TestCase):
    def test_default_frame_size_is_derived_from_dimensions(self):
        radar = RadarFrameConfig()

        self.assertEqual(192, radar.chirps_per_frame)
        self.assertEqual(12, radar.virtual_antennas)
        self.assertEqual(98304, radar.raw_values_per_frame)

    def test_invalid_frame_dimensions_fail_early(self):
        with self.assertRaises(ValueError):
            RadarFrameConfig(adc_samples=0)

    def test_dsp_channel_mapping_is_validated_against_radar(self):
        with self.assertRaises(ValueError):
            AppConfig(radar=RadarFrameConfig(tx_antennas=2))

    def test_current_iwr6843_cfg_can_build_its_own_runtime_config(self):
        shape = parse_radar_profile_shape("radar_configs/iwr6843.cfg")

        runtime_config = AppConfig().with_radar_shape(*shape.as_tuple())

        self.assertEqual(3, shape.tx_antennas)
        self.assertEqual(4, shape.rx_antennas)
        self.assertEqual(shape.as_tuple(), (
            runtime_config.radar.adc_samples,
            runtime_config.radar.chirps_per_tx,
            runtime_config.radar.tx_antennas,
            runtime_config.radar.rx_antennas,
        ))

    def test_app_config_adapts_to_selected_radar_cfg_shape(self):
        shape = RadarProfileShape(32, 64, 3, 4)

        runtime_config = AppConfig().with_radar_shape(*shape.as_tuple())

        self.assertEqual((32, 64, 3, 4), shape.as_tuple())
        self.assertEqual(49152, runtime_config.radar.raw_values_per_frame)

    def test_micro_doppler_cfg_matches_requested_radar_timing(self):
        config_path = "radar_configs/iwr6843_micro_doppler.cfg"
        shape = parse_radar_profile_shape(config_path)
        lines = [
            line.split()
            for line in Path(config_path).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("%")
        ]
        commands = {parts[0]: parts for parts in lines}
        profile = commands["profileCfg"]
        frame = commands["frameCfg"]
        channel = commands["channelCfg"]

        chirp_period_us = float(profile[3]) + float(profile[5])
        sample_time_us = int(profile[10]) / (float(profile[11]) / 1000)
        bandwidth_mhz = float(profile[8]) * sample_time_us
        chirps_per_frame = (int(frame[2]) - int(frame[1]) + 1) * int(frame[3])

        self.assertEqual((128, 128, 1, 1), shape.as_tuple())
        self.assertEqual((1, 1), (int(channel[1]), int(channel[2])))
        self.assertAlmostEqual(1000.0, bandwidth_mhz, places=6)
        self.assertAlmostEqual(3225.8, 1e6 / chirp_period_us, places=1)
        self.assertEqual(128, chirps_per_frame)
        self.assertAlmostEqual(25.0, 1000 / float(frame[5]), places=6)
        self.assertAlmostEqual(40.0, float(frame[5]), places=6)
        self.assertLess(chirps_per_frame * chirp_period_us / 1000, 40.0)

        runtime_config = AppConfig().with_radar_shape(
            *shape.as_tuple(), micro_doppler_only=True
        )
        self.assertTrue(runtime_config.micro_doppler_only)
        parameters = Iwr6843TlvParser().parse_config(config_path)
        self.assertEqual(128, parameters["numRangeBins"])
        self.assertEqual(128, parameters["numDopplerBins"])

    def test_profile_with_insufficient_virtual_antennas_is_rejected(self):
        shape = parse_radar_profile_shape("radar_configs/iwr1843.cfg")

        with self.assertRaisesRegex(ValueError, "天线映射"):
            AppConfig().with_radar_shape(*shape.as_tuple())

    def test_enhanced_cp2105_port_is_selected_for_radar_cli(self):
        ports = [
            SimpleNamespace(
                device="COM12",
                description="Standard Serial over Bluetooth link (COM12)",
                manufacturer="Microsoft",
                product=None,
                interface=None,
                hwid="BTHENUM\\device",
            ),
            SimpleNamespace(
                device="COM11",
                description=(
                    "Silicon Labs Dual CP2105 USB to UART Bridge: "
                    "Enhanced COM Port (COM11)"
                ),
                manufacturer="Silicon Labs",
                product=None,
                interface=None,
                hwid="USB VID:PID=10C4:EA70",
            ),
            SimpleNamespace(
                device="COM14",
                description=(
                    "Silicon Labs Dual CP2105 USB to UART Bridge: "
                    "Standard COM Port (COM14)"
                ),
                manufacturer="Silicon Labs",
                product=None,
                interface=None,
                hwid="USB VID:PID=10C4:EA70",
            ),
        ]

        preferred = select_preferred_cli_port(ports)

        self.assertEqual("COM11", preferred.device)


class RadarConfiguratorEngineTests(unittest.TestCase):
    def setUp(self):
        self.template_path = Path("radar_configs/iwr6843_micro_doppler.cfg")
        self.engine = Iwr6843ConfigEngine(self.template_path)

    def test_template_round_trip_preserves_capture_shape_and_commands(self):
        values = self.engine.default_values()
        generated = self.engine.render(values)
        commands = [
            line.split()[0]
            for line in generated.splitlines()
            if line.strip() and not line.startswith("%")
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir) / "generated_micro_doppler.cfg"
            target.write_text(generated, encoding="utf-8")
            shape = parse_radar_profile_shape(target)

        self.assertEqual((128, 128, 1, 1), shape.as_tuple())
        self.assertEqual(
            [
                "flushCfg",
                "dfeDataOutputMode",
                "channelCfg",
                "adcCfg",
                "adcbufCfg",
                "profileCfg",
                "chirpCfg",
                "frameCfg",
                "lowPower",
                "lvdsStreamCfg",
                "testSrcCfg",
                "sensorStart",
            ],
            commands,
        )

    def test_existing_config_values_drive_calculated_metrics(self):
        values = self.engine.default_values()
        metrics = self.engine.metrics(values)

        self.assertAlmostEqual(1000.0, metrics.bandwidth_mhz)
        self.assertAlmostEqual(25.0, metrics.frame_rate_hz)
        self.assertAlmostEqual(39.68, metrics.active_frame_time_ms)
        self.assertEqual((1, 1), (metrics.rx_antennas, metrics.tx_antennas))

    def test_invalid_adc_window_cannot_generate_config(self):
        values = self.engine.with_value(
            self.engine.default_values(), "ramp_end_time_us", 10
        )

        with self.assertRaises(ConfigValidationError):
            self.engine.render(values)

    def test_parameter_constraints_follow_cross_field_validation(self):
        values = self.engine.default_values()
        constraints = self.engine.parameter_constraints(values)

        self.assertEqual((60.0, 62.75), constraints["start_frequency_ghz"])
        self.assertEqual((38.0, 42.5), constraints["ramp_end_time_us"])
        self.assertAlmostEqual(39.68, constraints["frame_period_ms"][0])
        self.assertAlmostEqual(
            3764.705882,
            constraints["sample_rate_ksps"][0],
            places=6,
        )


class DcaControllerTests(unittest.TestCase):
    def test_dca_command_builder_rejects_unknown_command(self):
        with self.assertRaises(ValueError):
            Dca1000Controller.build_command("unknown")

    def test_dca_start_command_has_expected_wire_format(self):
        self.assertEqual(
            "5aa505000000aaee", Dca1000Controller.build_command("5").hex()
        )

    def test_dca_bind_error_is_explained_and_socket_is_closed(self):
        fake_socket = WinErrorSocket()

        with patch("hardware_interfaces.socket.socket", return_value=fake_socket):
            with self.assertRaisesRegex(
                HardwareConnectionError, "NetworkConfig.host_address"
            ):
                Dca1000Controller("test")

        self.assertTrue(fake_socket.closed)


class RadarCliClientTests(unittest.TestCase):
    def test_response_colors_match_cli_meaning(self):
        self.assertEqual("green", classify_cli_response("Done"))
        self.assertEqual("gray", classify_cli_response("Skipped"))
        self.assertEqual("gray", classify_cli_response("mmwDemo:/>"))
        self.assertEqual("red", classify_cli_response("Ignored: already stopped"))
        self.assertEqual(
            "red",
            classify_cli_response("'bad' is not recognized as a CLI command"),
        )

    def test_comment_and_separator_detection(self):
        self.assertTrue(is_radar_config_comment("% Created by Visualizer"))
        self.assertTrue(is_radar_config_comment("***************"))
        self.assertTrue(is_radar_config_comment(""))
        self.assertFalse(is_radar_config_comment("profileCfg 0 60"))

    def test_config_comments_are_logged_but_not_sent(self):
        serial_port = FakeSerialPort(
            [b"sensorStart\r\nDone\r\nmmwDemo:/>\r\n"]
        )
        log_entries = []
        client = RadarCliClient(
            "test",
            "COM11",
            log_callback=lambda message, color: log_entries.append(
                (message, color)
            ),
            serial_factory=lambda *args, **kwargs: serial_port,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = os.path.join(temp_dir, "radar.cfg")
            with open(config_path, "w", encoding="utf-8") as config_file:
                config_file.write(
                    "% Created for SDK ver:03.04\n"
                    "***************\n"
                    "sensorStart\n"
                )

            client.send_config(config_path)

        self.assertEqual([b"sensorStart\n"], serial_port.writes)
        self.assertIn(("注释: % Created for SDK ver:03.04", "gray"), log_entries)
        self.assertIn(("注释: ***************", "gray"), log_entries)
        self.assertIn(("发送: sensorStart", "blue"), log_entries)
        self.assertIn(("接收: Done", "green"), log_entries)

    def test_rejected_command_is_red_and_raises(self):
        serial_port = FakeSerialPort(
            [
                b"badCommand\r\n"
                b"'badCommand' is not recognized as a CLI command\r\n"
                b"mmwDemo:/>\r\n"
            ]
        )
        log_entries = []
        client = RadarCliClient(
            "test",
            "COM11",
            log_callback=lambda message, color: log_entries.append(
                (message, color)
            ),
            serial_factory=lambda *args, **kwargs: serial_port,
        )

        with self.assertRaises(RadarCliCommandError):
            client._send_line("badCommand")

        self.assertTrue(
            any(color == "red" and "not recognized" in message
                for message, color in log_entries)
        )


class DataProcessorTests(unittest.TestCase):
    def test_udp_listener_can_share_and_exit_cleanly(self):
        capture_buffer = ProcessStubCaptureBuffer()
        listener = UdpListener("test-listener", capture_buffer)

        listener.start()
        listener.join(timeout=5)

        self.assertFalse(listener.is_alive())
        self.assertEqual(7, capture_buffer.value[0])

    def test_decode_frame_uses_injected_radar_shape(self):
        radar = RadarFrameConfig(
            adc_samples=2,
            chirps_per_tx=2,
            tx_antennas=2,
            rx_antennas=2,
        )
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )
        raw = np.arange(radar.raw_values_per_frame, dtype=np.int16)

        decoded = processor._decode_frame(raw)

        self.assertEqual((2, 2, 4), decoded.shape)
        expected_first = raw.reshape(-1, 4)[0, 0] + 1j * raw.reshape(-1, 4)[0, 2]
        self.assertEqual(expected_first, decoded[0, 0, 0])

    def test_decode_frame_rejects_wrong_length(self):
        radar = RadarFrameConfig()
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )

        with self.assertRaises(ValueError):
            processor._decode_frame(np.zeros(4, dtype=np.int16))

    def test_decode_frame_supports_single_tx_single_rx_profile(self):
        radar = RadarFrameConfig(
            adc_samples=128,
            chirps_per_tx=128,
            tx_antennas=1,
            rx_antennas=1,
        )
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )

        decoded = processor._decode_frame(
            np.zeros(radar.raw_values_per_frame, dtype=np.int16)
        )

        self.assertEqual((128, 128, 1), decoded.shape)

    def test_micro_doppler_mode_skips_full_feature_pipeline(self):
        state = RuntimeState()
        state.set_feature_mode(FeatureMode.MICRO_DOPPLER)

        class SignalProcessorSpy:
            runtime_state = state

            def __init__(self):
                self.micro_calls = 0

            def process_micro_doppler(self, adc_frame, window_type_1d=None):
                self.micro_calls += 1
                return np.ones((8, 64))

            def process_time_features(self, *args, **kwargs):
                raise AssertionError("full time-feature DSP must not run")

            def process_angle_features(self, *args, **kwargs):
                raise AssertionError("angle DSP must not run")

        signal_processor = SignalProcessorSpy()
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=signal_processor,
            output_queue=Queue(),
        )

        result = processor._process_frame(object(), sequence=3)

        self.assertIsInstance(result, MicroDopplerFrame)
        self.assertEqual(3, result.sequence)
        self.assertEqual(1, signal_processor.micro_calls)

    def test_default_decoded_frame_runs_windowed_range_processing(self):
        radar = RadarFrameConfig()
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )
        decoded = processor._decode_frame(
            np.zeros(radar.raw_values_per_frame, dtype=np.int16)
        )
        signal_processor = RadarSignalProcessor(AppConfig(), RuntimeState())

        rti, rdi, dti = signal_processor.process_time_features(
            decoded, window_type_1d=Window.HANNING
        )

        self.assertEqual((64, 64, 12), decoded.shape)
        self.assertEqual((64, 64, 1), rti.shape)
        self.assertEqual((1, 64, 64, 12), rdi.shape)
        self.assertEqual((1, 64), dti.shape)

    def test_32_sample_profile_keeps_standard_feature_shapes(self):
        config = AppConfig().with_radar_shape(32, 64, 3, 4)
        radar = config.radar
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )
        decoded = processor._decode_frame(
            np.zeros(radar.raw_values_per_frame, dtype=np.int16)
        )
        signal_processor = RadarSignalProcessor(config, RuntimeState())

        rti, rdi, dti = signal_processor.process_time_features(
            decoded, window_type_1d=Window.HANNING
        )
        rai, rei = signal_processor.process_angle_features(decoded)

        self.assertEqual((64, 32, 12), decoded.shape)
        self.assertEqual((64, 64, 1), rti.shape)
        self.assertEqual((1, 64, 64, 12), rdi.shape)
        self.assertEqual((1, 64), dti.shape)
        self.assertEqual((1, 91, 64), rai.shape)
        self.assertEqual((1, 91, 64), rei.shape)

    def test_non_64_chirp_profile_is_processed_without_shape_error(self):
        config = AppConfig().with_radar_shape(32, 32, 3, 4)
        radar = config.radar
        processor = DataProcessor(
            "test",
            StubCaptureBuffer(),
            signal_processor=object(),
            output_queue=Queue(),
            radar_config=radar,
        )
        decoded = processor._decode_frame(
            np.zeros(radar.raw_values_per_frame, dtype=np.int16)
        )
        signal_processor = RadarSignalProcessor(config, RuntimeState())

        rti, rdi, dti = signal_processor.process_time_features(
            decoded, window_type_1d=Window.HANNING
        )

        self.assertEqual((32, 64, 1), rti.shape)
        self.assertEqual((1, 64, 32, 12), rdi.shape)
        self.assertEqual((1, 32), dti.shape)


class DspUtilityTests(unittest.TestCase):
    def test_windowing_broadcasts_on_non_last_axis(self):
        data = np.ones((2, 3, 4))

        result = utils.windowing(data, Window.HANNING, axis=1)

        expected = np.hanning(3).reshape(1, 3, 1) * data
        np.testing.assert_array_equal(expected, result)

    def test_doppler_indices_support_vector_conversion(self):
        result = DOPPLER_IDX_TO_SIGNED(np.array([0, 31, 32, 63]), 64)

        np.testing.assert_array_equal([0, 31, -32, -1], result)

    def test_angle_helpers_run_without_removed_numpy_aliases(self):
        num_vectors, steering_vectors = gen_steering_vec(1, 1, 2)
        num_peaks, peak_indices, total_power = peak_search(
            np.array([0.0, 1.0, 0.0, 2.0, 0.0])
        )

        self.assertEqual(3, num_vectors)
        self.assertEqual((3, 2), steering_vectors.shape)
        self.assertEqual(2, num_peaks)
        np.testing.assert_array_equal([1, 3], peak_indices)
        self.assertEqual(3.0, total_power)

    @unittest.skipUnless(
        importlib.util.find_spec("scipy") is not None,
        "optional SciPy dependency unavailable",
    )
    def test_zoom_fft_accepts_builtin_integer_sample_count(self):
        zoom_fft = ZoomFFT(10, 20, 100, np.ones(100))
        zoom_fft.original_sample_range = 1

        _, _, fft_length, _, _ = zoom_fft.compute_zoomfft(10)

        self.assertEqual(10, fft_length)


class SignalProcessorTests(unittest.TestCase):
    def test_default_processor_emits_expected_feature_shapes(self):
        random = np.random.RandomState(7)
        data = random.randn(64, 64, 12) + 1j * random.randn(64, 64, 12)
        processor = RadarSignalProcessor(AppConfig(), RuntimeState())

        rti, rdi, dti = processor.process_time_features(data)
        rai, rei = processor.process_angle_features(data)

        self.assertEqual((64, 64, 1), rti.shape)
        self.assertEqual((1, 64, 64, 12), rdi.shape)
        self.assertEqual((1, 64), dti.shape)
        self.assertEqual((1, 91, 64), rai.shape)
        self.assertEqual((1, 91, 64), rei.shape)

    def test_zero_frame_does_not_crash_angle_processing(self):
        processor = RadarSignalProcessor(AppConfig(), RuntimeState())
        data = np.zeros((64, 64, 12), dtype=np.complex128)

        rai, rei = processor.process_angle_features(data)

        self.assertTrue(np.isfinite(rai).all())
        self.assertTrue(np.isfinite(rei).all())

    def test_micro_doppler_uses_only_configured_receive_channel(self):
        random = np.random.RandomState(9)
        other_rx_only = np.zeros((64, 64, 12), dtype=np.complex128)
        other_rx_only[:, :, 1] = random.randn(64, 64) + 1j * random.randn(64, 64)
        processor = RadarSignalProcessor(AppConfig(), RuntimeState())

        processor.process_micro_doppler(
            other_rx_only, clutter_removal_enabled=False
        )
        ignored_result = processor.process_micro_doppler(
            other_rx_only, clutter_removal_enabled=False
        )

        np.testing.assert_array_equal(0, ignored_result)

        selected_rx = np.zeros((64, 64, 12), dtype=np.complex128)
        selected_rx[:, :, 0] = random.randn(64, 64) + 1j * random.randn(64, 64)
        selected_processor = RadarSignalProcessor(AppConfig(), RuntimeState())
        selected_processor.process_micro_doppler(
            selected_rx, clutter_removal_enabled=False
        )
        selected_result = selected_processor.process_micro_doppler(
            selected_rx, clutter_removal_enabled=False
        )

        self.assertEqual((1, 128), selected_result.shape)
        self.assertGreater(np.count_nonzero(selected_result), 0)

    def test_micro_doppler_flattens_frames_with_configured_hop(self):
        config = AppConfig().with_radar_shape(
            128, 128, 1, 1, micro_doppler_only=True
        )
        processor = RadarSignalProcessor(config, RuntimeState())
        random = np.random.RandomState(11)
        frame = random.randn(128, 128, 1) + 1j * random.randn(128, 128, 1)

        first = processor.process_micro_doppler(frame)
        second = processor.process_micro_doppler(frame)
        third = processor.process_micro_doppler(frame)

        self.assertEqual((1, 128), first.shape)
        self.assertEqual((5, 128), second.shape)
        self.assertEqual((9, 128), third.shape)
        self.assertEqual(96, processor._micro_doppler_chirp_buffer.shape[0])
        self.assertTrue(np.isfinite(third).all())

    def test_noncanonical_adc_layout_is_rejected(self):
        processor = RadarSignalProcessor(AppConfig(), RuntimeState())

        with self.assertRaisesRegex(ValueError, "ADC frame must use"):
            processor.process_time_features(
                np.zeros((64, 12, 64)), window_type_1d=Window.HANNING
            )


@unittest.skipUnless(UI_DEPENDENCIES_AVAILABLE, "optional UI dependencies unavailable")
class ApplicationLifecycleTests(unittest.TestCase):
    def test_configurator_is_mounted_in_its_dock_and_loads_template(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        application.qt_app.processEvents()
        try:
            panel = application.radar_config_panel
            self.assertIs(panel, application.ui.radarConfigDock.contentWidget)
            self.assertEqual(
                "iwr6843_micro_doppler.cfg",
                Path(panel.selected_config_path()).name,
            )
            self.assertEqual(
                128,
                int(panel.parameter_controls["adc_samples"].value()),
            )
            start_frequency = panel.parameter_controls["start_frequency_ghz"]
            self.assertIsInstance(start_frequency.layout(), QtWidgets.QHBoxLayout)
            self.assertLess(
                start_frequency.layout().indexOf(start_frequency.label),
                start_frequency.layout().indexOf(start_frequency.slider),
            )
            self.assertLess(
                start_frequency.layout().indexOf(start_frequency.slider),
                start_frequency.layout().indexOf(start_frequency.spin),
            )
            self.assertEqual(60.0, start_frequency.slider.valid_minimum)
            self.assertEqual(62.75, start_frequency.slider.valid_maximum)

            ramp_end = panel.parameter_controls["ramp_end_time_us"]
            panel._parameter_changed("ramp_end_time_us", 10)
            self.assertTrue(ramp_end.slider._invalid)
            self.assertIn("#ff7b82", ramp_end.spin.styleSheet())
            panel._parameter_changed("ramp_end_time_us", 40)
            self.assertFalse(ramp_end.slider._invalid)
            self.assertIn("#ffffff", ramp_end.spin.styleSheet())

            panel._parameter_changed("frame_period_ms", 41)

            self.assertTrue(panel.uses_generated_config())
            self.assertIn("frameCfg 0 0 128 0 41 1 0", panel.current_config_text())
            panel.refresh_config_files()
            self.assertEqual(
                41,
                int(panel.parameter_controls["frame_period_ms"].value()),
            )

            panel.source_combo.setCurrentIndex(
                panel.source_combo.findData(panel.EXISTING_SOURCE)
            )

            self.assertEqual(
                40,
                int(panel.parameter_controls["frame_period_ms"].value()),
            )
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_channel_masks_are_edited_with_named_checkboxes(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        application.qt_app.processEvents()
        try:
            panel = application.radar_config_panel
            self.assertEqual(
                ["RX1", "RX2", "RX3", "RX4"],
                [checkbox.text() for checkbox in panel.rx_channel_checks],
            )
            self.assertEqual(
                ["TX1", "TX2", "TX3"],
                [checkbox.text() for checkbox in panel.tx_channel_checks],
            )
            self.assertEqual(
                [True, False, False, False],
                [checkbox.isChecked() for checkbox in panel.rx_channel_checks],
            )
            self.assertEqual(
                [True, False, False],
                [checkbox.isChecked() for checkbox in panel.tx_channel_checks],
            )

            panel.rx_channel_checks[1].setChecked(True)
            panel.tx_channel_checks[2].setChecked(True)
            panel.tx_channel_checks[0].setChecked(False)

            self.assertEqual(3, panel.values.rx_channel_mask)
            self.assertEqual(4, panel.values.tx_channel_mask)
            self.assertIn("channelCfg 3 4 0", panel.current_config_text())

            panel.tx_channel_checks[2].setChecked(False)
            self.assertEqual(0, panel.values.tx_channel_mask)
            self.assertFalse(panel.send_button.isEnabled())
            self.assertIn("#b42318", panel.tx_channel_checks[0].styleSheet())
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_generated_config_is_sent_from_a_temporary_file(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        panel = application.radar_config_panel
        panel.source_combo.setCurrentIndex(
            panel.source_combo.findData(panel.GENERATED_SOURCE)
        )
        application.cli_port_name = "COM11"
        captured = {}

        def capture_config(config_path, com_port):
            captured["path"] = config_path
            captured["text"] = Path(config_path).read_text(encoding="utf-8")
            captured["port"] = com_port

        try:
            with patch.object(application, "_apply_radar_profile") as apply_profile:
                with patch.object(
                    application, "open_radar", side_effect=capture_config
                ):
                    application.send_radar_config()

            self.assertEqual("COM11", captured["port"])
            self.assertIn("profileCfg", captured["text"])
            self.assertIn("lvdsStreamCfg", captured["text"])
            self.assertFalse(Path(captured["path"]).exists())
            apply_profile.assert_called_once()
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_single_channel_saved_config_is_inferred_as_micro_doppler(self):
        import main

        application = main.RadarStreamApplication()
        application.cli_port_name = "COM11"
        source = Path("radar_configs/iwr6843_micro_doppler.cfg").read_text(
            encoding="utf-8"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "saved_without_mode_marker.cfg"
            config_path.write_text(source, encoding="utf-8")
            with patch.object(application, "_apply_radar_profile") as apply_profile:
                with patch.object(application, "open_radar"):
                    with patch.object(application, "print_log"):
                        application._send_radar_config_file(str(config_path))

        self.assertTrue(apply_profile.call_args[1]["micro_doppler_only"])

    def test_application_run_does_not_initialize_capture_hardware(self):
        import main

        class ImmediateQtApplication:
            @staticmethod
            def exec_():
                return 0

        application = main.RadarStreamApplication()

        def build_ui_without_event_loop():
            application.qt_app = ImmediateQtApplication()

        application._build_ui = build_ui_without_event_loop
        with patch.object(
            main,
            "CaptureBuffer",
            side_effect=AssertionError("capture hardware initialized at startup"),
        ), patch.object(
            main,
            "Dca1000Controller",
            side_effect=AssertionError("DCA1000 initialized at startup"),
        ):
            self.assertEqual(0, application.run())

        self.assertIsNone(application.capture_buffer)
        self.assertIsNone(application.dca1000)

    def test_default_cli_port_is_enhanced_port(self):
        import main

        ports = [
            SimpleNamespace(
                device="COM14",
                description="CP2105 Standard COM Port",
                manufacturer="Silicon Labs",
                product=None,
                interface=None,
                hwid="USB VID:PID=10C4:EA70",
            ),
            SimpleNamespace(
                device="COM11",
                description="CP2105 Enhanced COM Port",
                manufacturer="Silicon Labs",
                product=None,
                interface=None,
                hwid="USB VID:PID=10C4:EA70",
            ),
        ]
        with patch.object(main.list_ports, "comports", return_value=ports):
            application = main.RadarStreamApplication()
            application._build_ui()
        try:
            self.assertEqual("COM11", application.cli_port_name)
            self.assertEqual(
                "COM11", application.radar_config_panel.cli_combo.currentText()
            )
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_profile_change_rebuilds_runtime_processing_config(self):
        import main

        application = main.RadarStreamApplication()
        application.ui = SimpleNamespace(config=application.config)
        with patch.object(application, "_stop_hardware") as stop_hardware:
            application._apply_radar_profile(RadarProfileShape(32, 64, 3, 4))

        stop_hardware.assert_called_once_with()
        self.assertEqual(32, application.config.radar.adc_samples)
        self.assertEqual(49152, application.config.radar.raw_values_per_frame)
        self.assertEqual(application.config, application.signal_processor.config)
        self.assertEqual(application.config, application.ui.config)

    def test_main_window_contains_four_independent_docks(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        try:
            docks = (
                application.ui.radarDataDock,
                application.ui.radarConfigDock,
                application.ui.captureDock,
                application.ui.logDock,
            )
            required_features = (
                QtWidgets.QDockWidget.DockWidgetClosable
                | QtWidgets.QDockWidget.DockWidgetMovable
                | QtWidgets.QDockWidget.DockWidgetFloatable
            )
            self.assertEqual(4, len(docks))
            for dock in docks:
                self.assertEqual(
                    required_features,
                    dock.features() & required_features,
                )
                self.assertIn(
                    dock.toggleViewAction(), application.ui.windowMenu.actions()
                )
                self.assertIn("border: 1px", dock.customTitleBar.styleSheet())
                self.assertIn("border: 1px", dock.contentFrame.styleSheet())
                self.assertIn("border-top: 0", dock.contentFrame.styleSheet())

            self.assertTrue(application.ui.displayModeGroup.isExclusive())
            self.assertEqual(2, len(application.ui.displayModeGroup.actions()))
            self.assertFalse(hasattr(application.ui, "tabWidget"))
            self.assertEqual("日志显示", application.ui.logDock.windowTitle())
            application.ui.logTextEdit.setPlainText("temporary log")
            application.ui.clearLogButton.click()
            self.assertEqual("", application.ui.logTextEdit.toPlainText())

            guide = application.ui.dockGuideOverlay
            guide.begin_drag(application.ui.radarConfigDock)
            target = guide.guide_rects()[QtCore.Qt.LeftDockWidgetArea].center()
            guide.update_drag(guide.mapToGlobal(target))
            self.assertTrue(guide.isVisible())
            self.assertEqual(QtCore.Qt.LeftDockWidgetArea, guide.target_area)
            guide.finish_drag(guide.mapToGlobal(target))
            application.qt_app.processEvents()
            self.assertEqual(
                QtCore.Qt.LeftDockWidgetArea,
                application.main_window.dockWidgetArea(
                    application.ui.radarConfigDock
                ),
            )
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_title_drag_uses_guide_and_restores_previous_dock_ratio(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        application.qt_app.processEvents()
        data_dock = application.ui.radarDataDock
        config_dock = application.ui.radarConfigDock
        title_bar = data_dock.customTitleBar
        before_widths = (data_dock.width(), config_dock.width())

        def mouse_event(kind, local, global_position, button, buttons):
            return QtGui.QMouseEvent(
                kind,
                QtCore.QPointF(local),
                QtCore.QPointF(global_position),
                button,
                buttons,
                QtCore.Qt.NoModifier,
            )

        try:
            start_local = title_bar.rect().center()
            start_global = title_bar.mapToGlobal(start_local)
            title_bar.mousePressEvent(
                mouse_event(
                    QtCore.QEvent.MouseButtonPress,
                    start_local,
                    start_global,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.LeftButton,
                )
            )
            move_global = start_global + QtCore.QPoint(30, 10)
            title_bar.mouseMoveEvent(
                mouse_event(
                    QtCore.QEvent.MouseMove,
                    title_bar.mapFromGlobal(move_global),
                    move_global,
                    QtCore.Qt.NoButton,
                    QtCore.Qt.LeftButton,
                )
            )

            guide = application.ui.dockGuideOverlay
            self.assertTrue(data_dock.isFloating())
            self.assertTrue(guide.isVisible())
            target_global = guide.mapToGlobal(
                guide.guide_rects()[QtCore.Qt.LeftDockWidgetArea].center()
            )
            title_bar.mouseMoveEvent(
                mouse_event(
                    QtCore.QEvent.MouseMove,
                    title_bar.mapFromGlobal(target_global),
                    target_global,
                    QtCore.Qt.NoButton,
                    QtCore.Qt.LeftButton,
                )
            )
            title_bar.mouseReleaseEvent(
                mouse_event(
                    QtCore.QEvent.MouseButtonRelease,
                    title_bar.mapFromGlobal(target_global),
                    target_global,
                    QtCore.Qt.LeftButton,
                    QtCore.Qt.NoButton,
                )
            )
            for _ in range(3):
                application.qt_app.processEvents()

            self.assertFalse(data_dock.isFloating())
            self.assertFalse(guide.isVisible())
            self.assertEqual(
                QtCore.Qt.LeftDockWidgetArea,
                application.main_window.dockWidgetArea(data_dock),
            )
            self.assertAlmostEqual(before_widths[0], data_dock.width(), delta=2)
            self.assertAlmostEqual(before_widths[1], config_dock.width(), delta=2)
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_feature_views_fill_responsive_grid_cells(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        application.qt_app.processEvents()
        try:
            view_names = {
                "rdi": "rangeDopplerView",
                "rai": "rangeAzimuthView",
                "rti": "rangeTimeView",
                "dti": "dopplerTimeView",
                "rei": "rangeElevationView",
            }
            expected_grid_positions = {
                "rangeTimeView": (0, 0, 1, 1),
                "dopplerTimeView": (0, 1, 1, 1),
                "rangeElevationView": (0, 2, 1, 1),
                "rangeDopplerView": (1, 0, 1, 1),
                "rangeAzimuthView": (1, 1, 1, 1),
            }
            for feature_name, widget_name in view_names.items():
                widget = getattr(application.ui, widget_name)
                view = application.feature_views[feature_name]
                self.assertIs(view, widget.centralWidget)
                self.assertIn("border: 1px", widget.styleSheet())
                self.assertGreater(widget.maximumWidth(), 255)
                self.assertAlmostEqual(widget.width(), view.width(), delta=2)
                self.assertAlmostEqual(widget.height(), view.height(), delta=2)
                cell = widget.parentWidget()
                grid_index = application.ui.featureGrid.indexOf(cell)
                self.assertEqual(
                    expected_grid_positions[widget_name],
                    application.ui.featureGrid.getItemPosition(grid_index),
                )

                image = application.images[feature_name]
                image.setImage(np.ones((10, 20)))

            reference_width = application.ui.rangeTimeView.width()
            for widget_name in expected_grid_positions:
                self.assertAlmostEqual(
                    reference_width,
                    getattr(application.ui, widget_name).width(),
                    delta=2,
                )

            application.qt_app.processEvents()
            for feature_name in view_names:
                image = application.images[feature_name]
                view_range = application.feature_views[feature_name].viewRange()
                image_rect = image.boundingRect()
                np.testing.assert_allclose(
                    view_range[0], [image_rect.left(), image_rect.right()]
                )
                np.testing.assert_allclose(
                    view_range[1], [image_rect.top(), image_rect.bottom()]
                )
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_display_menu_switches_processing_path_and_updates_strip(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        try:
            application.ui.microDopplerAction.trigger()
            application.qt_app.processEvents()

            self.assertTrue(application.ui.microDopplerAction.isChecked())
            self.assertFalse(application.ui.fullFeatureAction.isChecked())
            self.assertIs(
                application.ui.microDopplerPage,
                application.ui.dataDisplayStack.currentWidget(),
            )
            self.assertIs(
                FeatureMode.MICRO_DOPPLER,
                application.runtime_state.feature_mode,
            )
            self.assertIs(
                application.feature_views["micro_doppler"],
                application.ui.microDopplerView.centralWidget,
            )

            feature = np.ones((128, 64))
            application.feature_queue.put_nowait(MicroDopplerFrame(1, feature))
            application.update_figure()
            np.testing.assert_array_equal(
                feature, application.images["micro_doppler"].image
            )
            self.assertEqual(
                (1.0, 2.0),
                application._robust_micro_doppler_levels(feature),
            )

            application.ui.fullFeatureAction.trigger()
            application.qt_app.processEvents()
            self.assertIs(
                application.ui.fullFeaturePage,
                application.ui.dataDisplayStack.currentWidget(),
            )
            self.assertIs(FeatureMode.FULL, application.runtime_state.feature_mode)
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()

    def test_single_channel_profile_forces_micro_doppler_menu_mode(self):
        import main

        application = main.RadarStreamApplication()
        application._build_ui()
        try:
            shape = parse_radar_profile_shape(
                "radar_configs/iwr6843_micro_doppler.cfg"
            )
            application._apply_radar_profile(
                shape,
                micro_doppler_only=True,
            )

            self.assertTrue(application.config.micro_doppler_only)
            self.assertFalse(application.ui.fullFeatureAction.isEnabled())
            self.assertTrue(application.ui.microDopplerAction.isChecked())
            self.assertIs(
                application.ui.microDopplerPage,
                application.ui.dataDisplayStack.currentWidget(),
            )
            self.assertIs(
                FeatureMode.MICRO_DOPPLER,
                application.runtime_state.feature_mode,
            )
        finally:
            application.refresh_timer.stop()
            application.capture_interval_timer.stop()
            application.main_window.close()
            application.shutdown()


if __name__ == "__main__":
    unittest.main()
