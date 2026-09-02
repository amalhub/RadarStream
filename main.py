"""RadarStream application entry point and UI controller."""

import html
import os
from pathlib import Path
import sys
import tempfile
import time
from queue import Empty, Queue

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")

import numpy as np
import pyqtgraph as pg
from PyQt5 import QtWidgets
from pyqtgraph.Qt import QtCore, QtGui
from serial.tools import list_ports

from app_config import DEFAULT_CONFIG
from colormap_utils import get_matplotlib_colormap, pg_get_cmap
from data_pipeline import (
    CaptureBuffer,
    DataProcessor,
    FeatureFrame,
    MicroDopplerFrame,
    UdpListener,
)
from hardware_interfaces import (
    Dca1000Controller,
    RadarCliClient,
    select_preferred_cli_port,
)
from radar_profile import parse_radar_profile_shape
from radar_configurator.widget import RadarConfigPanel
from runtime_state import FeatureMode, RuntimeState
from signal_processor import RadarSignalProcessor
from main_window_ui import Ui_MainWindow


class RadarStreamApplication:
    """Compose hardware, processing, runtime state and PyQt widgets."""

    def __init__(self, config=DEFAULT_CONFIG):
        self.base_config = config
        self.config = config
        self.runtime_state = RuntimeState()

        self.feature_queue = Queue(maxsize=config.feature_queue_size)
        self.signal_processor = RadarSignalProcessor(config, self.runtime_state)
        self.capture_buffer = None
        self.dca1000 = None
        self.collector = None
        self.processor = None
        self.radar_ctrl = None
        self.latest_features = None

        self.cli_port_name = ""
        self.dataset_scene_dir = None
        self.capture_index = 0

        self.qt_app = None
        self.main_window = None
        self.ui = None
        self.radar_config_panel = None
        self.images = {}
        self.feature_views = {}

    def run(self):
        exit_code = 1
        try:
            self._build_ui()
            exit_code = self.qt_app.exec_()
        finally:
            self.shutdown()
        return exit_code

    def _ensure_capture_backend(self):
        """Connect DCA1000 and create capture threads on first hardware use."""

        if self.dca1000 is not None:
            return

        capture_buffer = None
        dca1000 = None
        collector = None
        try:
            capture_buffer = CaptureBuffer(
                self.config.radar.raw_values_per_frame,
                self.config.paths.capture_library,
            )
            dca1000 = Dca1000Controller(
                "Dca1000Controller", settings=self.config.network
            )
            collector = UdpListener("Listener", capture_buffer)
            processor = DataProcessor(
                "Processor",
                capture_buffer,
                self.signal_processor,
                self.feature_queue,
                self.config.radar,
            )
            collector.start()
        except Exception:
            if dca1000 is not None:
                try:
                    dca1000.close()
                except Exception:
                    pass
            if collector is not None and collector.ident is not None:
                collector.stop()
                collector.join(timeout=1)
            raise

        # Commit only after every initialization step succeeds. A failed
        # connection therefore leaves the application in a retryable state.
        self.capture_buffer = capture_buffer
        self.dca1000 = dca1000
        self.collector = collector
        self.processor = processor

    def _build_ui(self):
        self.qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.main_window = QtWidgets.QMainWindow()
        self.ui = Ui_MainWindow(self.config, self.runtime_state)
        self.ui.setupUi(self.main_window)
        self._install_radar_config_panel()

        self._configure_feature_views()
        self._connect_signals()
        self._update_display_mode_availability()
        self.update_com_ports()

        self.refresh_timer = QtCore.QTimer(self.main_window)
        self.refresh_timer.timeout.connect(self.update_figure)
        self.refresh_timer.start(self.config.ui_refresh_milliseconds)

        self.capture_interval_timer = QtCore.QTimer(self.main_window)
        self.capture_interval_timer.timeout.connect(
            self.runtime_state.open_capture_interval
        )
        self.capture_interval_timer.start(
            self.config.capture_interval_milliseconds
        )
        self.runtime_state.open_capture_interval()
        self.print_log("Welcome!", "green")
        self.main_window.show()

    def _install_radar_config_panel(self):
        """Mount the reusable cfg component in its own dock."""

        template_path = (
            self.config.paths.radar_config_dir
            / "iwr6843_micro_doppler.cfg"
        )
        panel = RadarConfigPanel(
            template_path=template_path,
            config_dir=self.config.paths.radar_config_dir,
            parent=self.ui.radarConfigDock,
        )
        self.ui.set_dock_content(self.ui.radarConfigDock, panel)
        self.radar_config_panel = panel

    def _configure_feature_views(self):
        view_names = {
            "rdi": "rangeDopplerView",
            "rai": "rangeAzimuthView",
            "rti": "rangeTimeView",
            "dti": "dopplerTimeView",
            "rei": "rangeElevationView",
        }
        color_map = pg_get_cmap("customize")
        lookup_table = color_map.getLookupTable(0.0, 1.0, 256)

        feature_widgets = [
            getattr(self.ui, widget_name) for widget_name in view_names.values()
        ]
        for widget in feature_widgets:
            widget.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding,
                QtWidgets.QSizePolicy.Expanding,
            )
            widget.setMinimumSize(QtCore.QSize(160, 160))
            widget.setMaximumSize(QtCore.QSize(16777215, 16777215))
        for feature_name, widget_name in view_names.items():
            widget = getattr(self.ui, widget_name)
            # GraphicsLayoutWidget already owns a GraphicsLayout. Adding a
            # ViewBox to that layout and then setting the same ViewBox as the
            # central widget makes two layout systems fight over its geometry,
            # shrinking it to a strip in the top-left corner. Replace the
            # central item once with a standalone ViewBox instead.
            view = pg.ViewBox(enableMenu=False)
            view.setDefaultPadding(0.0)
            view.setAspectLocked(False)
            view.setMouseEnabled(x=False, y=False)
            widget.setCentralItem(view)
            image = pg.ImageItem(border=None)
            image.setLookupTable(lookup_table)
            view.addItem(image)
            view.enableAutoRange(x=True, y=True)
            self.feature_views[feature_name] = view
            self.images[feature_name] = image

        micro_doppler_view = pg.ViewBox(enableMenu=False)
        micro_doppler_view.setDefaultPadding(0.0)
        micro_doppler_view.setAspectLocked(False)
        micro_doppler_view.setMouseEnabled(x=False, y=False)
        self.ui.microDopplerView.setCentralItem(micro_doppler_view)
        micro_doppler_image = pg.ImageItem(border=None)
        micro_doppler_image.setLookupTable(lookup_table)
        micro_doppler_view.addItem(micro_doppler_image)
        micro_doppler_view.enableAutoRange(x=True, y=True)
        self.feature_views["micro_doppler"] = micro_doppler_view
        self.images["micro_doppler"] = micro_doppler_image

    def _connect_signals(self):
        self.radar_config_panel.cli_combo.popupAboutToShow.connect(
            self.update_com_ports
        )
        self.radar_config_panel.cli_combo.currentIndexChanged.connect(
            self.set_serial_port
        )
        self.ui.colorMapCombo.currentIndexChanged.connect(self.set_color)
        self.radar_config_panel.configSelectionChanged.connect(
            self.show_radar_parameters
        )
        self.radar_config_panel.configSaved.connect(
            lambda path: self.print_log("配置已保存：{}".format(path), "green")
        )
        self.ui.captureSceneCombo.currentIndexChanged.connect(
            self.select_dataset_scene
        )
        self.ui.captureSubjectEdit.editingFinished.connect(
            self.select_dataset_scene
        )
        self.ui.captureButton.toggled.connect(self._set_capture_enabled)
        self.radar_config_panel.sendRequested.connect(self.send_radar_config)
        self.radar_config_panel.exitRequested.connect(self.qt_app.exit)
        self.ui.fullFeatureAction.triggered.connect(
            lambda checked: checked and self._set_display_mode(FeatureMode.FULL)
        )
        self.ui.microDopplerAction.triggered.connect(
            lambda checked: checked
            and self._set_display_mode(FeatureMode.MICRO_DOPPLER)
        )

    def _set_display_mode(self, mode):
        """Select one DSP/display path from the mutually exclusive menu."""

        if mode is FeatureMode.FULL and self.config.micro_doppler_only:
            self.ui.microDopplerAction.setChecked(True)
            mode = FeatureMode.MICRO_DOPPLER

        page = (
            self.ui.microDopplerPage
            if mode is FeatureMode.MICRO_DOPPLER
            else self.ui.fullFeaturePage
        )
        action = (
            self.ui.microDopplerAction
            if mode is FeatureMode.MICRO_DOPPLER
            else self.ui.fullFeatureAction
        )
        action.setChecked(True)
        self.ui.dataDisplayStack.setCurrentWidget(page)

        if (
            mode is FeatureMode.MICRO_DOPPLER
            and self.runtime_state.feature_mode is not mode
        ):
            self.signal_processor.reset_micro_doppler_history()
            self.images["micro_doppler"].clear()
        self.runtime_state.set_feature_mode(mode)
        self.latest_features = None
        try:
            while True:
                self.feature_queue.get_nowait()
        except Empty:
            pass

        capture_available = mode is FeatureMode.FULL
        if not capture_available and self.ui.captureButton.isChecked():
            self.ui.captureButton.setChecked(False)
        self.ui.captureButton.setEnabled(capture_available)
        self.ui.captureButton.setToolTip(
            "" if capture_available else "采集仅适用于多维特征显示模式"
        )

    def _update_display_mode_availability(self):
        """Keep display choices compatible with the active radar profile."""

        self.ui.fullFeatureAction.setEnabled(not self.config.micro_doppler_only)
        if self.config.micro_doppler_only:
            self._set_display_mode(FeatureMode.MICRO_DOPPLER)
        elif self.ui.microDopplerAction.isChecked():
            self._set_display_mode(FeatureMode.MICRO_DOPPLER)
        else:
            self._set_display_mode(FeatureMode.FULL)

    def update_figure(self):
        try:
            while True:
                self.latest_features = self.feature_queue.get_nowait()
        except Empty:
            pass

        if self.latest_features is None:
            return

        features = self.latest_features
        if self.runtime_state.feature_mode is FeatureMode.MICRO_DOPPLER:
            if not isinstance(features, MicroDopplerFrame):
                return
            display_levels = self._robust_micro_doppler_levels(
                features.micro_doppler
            )
            self.images["micro_doppler"].setImage(
                features.micro_doppler,
                levels=display_levels,
            )
            return

        if not isinstance(features, FeatureFrame):
            return
        dsp_config = self.config.dsp
        history_slice = slice(
            dsp_config.angle_history_start, dsp_config.angle_history_stop
        )
        rti_feature = features.rti.sum(2)[::dsp_config.rti_display_stride, :]
        self.images["rti"].setImage(rti_feature, levels=self.config.rti_levels)
        self.images["rdi"].setImage(
            features.rdi.sum(0)[:, :, 0].T, levels=self.config.rdi_levels
        )
        self.images["rei"].setImage(
            features.rei[history_slice].sum(0).T,
            levels=self.config.angle_levels,
        )
        self.images["dti"].setImage(features.dti, levels=self.config.dti_levels)
        self.images["rai"].setImage(
            features.rai[history_slice].sum(0),
            levels=self.config.angle_levels,
        )

        if (
            self.runtime_state.consume_capture()
            and self.ui.captureButton.isChecked()
            and self.dataset_scene_dir is not None
        ):
            self._save_feature_views(
                (
                    rti_feature,
                    features.dti,
                    features.rdi[:, :, :, 0],
                    features.rai,
                    features.rei,
                )
            )

    @staticmethod
    def _robust_micro_doppler_levels(feature):
        """Return visible color levels without letting outliers dominate."""

        finite_values = np.asarray(feature)[np.isfinite(feature)]
        if finite_values.size == 0:
            return (0.0, 1.0)
        lower, upper = np.percentile(finite_values, (2.0, 99.5))
        if upper <= lower:
            lower = float(finite_values.min())
            upper = float(finite_values.max())
        if upper <= lower:
            upper = lower + 1.0
        return (float(lower), float(upper))


    def _set_capture_enabled(self, enabled):
        """Start or stop event-triggered feature capture."""

        if enabled:
            self.select_dataset_scene()
            if self.dataset_scene_dir is None:
                self.ui.captureButton.setChecked(False)
                self.print_log("采集失败：请填写数据集和场景", "red")
                return
            self.runtime_state.set_capture_enabled(True)
            self.runtime_state.open_capture_interval()
        else:
            self.runtime_state.set_capture_enabled(False)
        self.ui.captureButton.setText("停止采集" if enabled else "开始采集")
        self.print_log("采集已启动" if enabled else "采集已停止", "green")

    def _save_feature_views(self, feature_views):
        self.capture_index += 1
        names = ("RT", "DT", "RDT", "ART", "ERT")
        for name, feature in zip(names, feature_views):
            file_name = "{}_feature_{:05d}.npy".format(name, self.capture_index)
            np.save(str(self.dataset_scene_dir / file_name), feature)
        self.print_log(
            "采集到特征:{}-{:05d}".format(
                self.ui.captureSceneCombo.currentText(), self.capture_index
            ),
            "blue",
        )

    def select_dataset_scene(self):
        subject = self.ui.captureSubjectEdit.text().strip()
        scene = self.ui.captureSceneCombo.currentText().strip()
        if not subject or not scene:
            self.dataset_scene_dir = None
            return

        self.dataset_scene_dir = self.config.paths.dataset_dir / subject / scene
        self.dataset_scene_dir.mkdir(parents=True, exist_ok=True)
        self.capture_index = len(list(self.dataset_scene_dir.glob("DT_feature_*.npy")))

    def show_radar_parameters(self, config_path=""):
        config_path = config_path or self.radar_config_panel.selected_config_path()
        self.radar_config_panel.config_combo.setToolTip(config_path)

    def update_com_ports(self):
        ports = list(list_ports.comports())
        preferred = select_preferred_cli_port(ports)
        previous = self.cli_port_name

        available_devices = [port.device for port in ports]
        selected_device = previous
        if selected_device not in available_devices:
            selected_device = preferred.device if preferred is not None else ""
        self.radar_config_panel.set_cli_ports(ports, selected_device)
        self.cli_port_name = self.radar_config_panel.selected_cli_port()

    def set_serial_port(self):
        self.cli_port_name = self.radar_config_panel.selected_cli_port()

    def send_radar_config(self):
        if not self.cli_port_name:
            self.print_log("发送失败：请先选择 CLI 串口", "red")
            return
        if self.radar_config_panel.uses_generated_config():
            try:
                config_text = self.radar_config_panel.generated_config_text()
                micro_doppler_only = (
                    self.radar_config_panel.generated_is_micro_doppler_only()
                )
            except Exception as error:
                self.print_log("发送失败：{}".format(error), "red")
                return
            with tempfile.TemporaryDirectory(prefix="radarstream_cfg_") as temp_dir:
                suffix = "_micro_doppler" if micro_doppler_only else ""
                config_path = Path(temp_dir) / (
                    "iwr6843_generated{}.cfg".format(suffix)
                )
                with config_path.open("w", encoding="utf-8", newline="\n") as stream:
                    stream.write(config_text)
                self._send_radar_config_file(
                    str(config_path),
                    micro_doppler_only=micro_doppler_only,
                )
            return

        config_path = self.radar_config_panel.selected_config_path()
        if not config_path:
            self.print_log("发送失败：请先选择配置文件", "red")
            return
        self._send_radar_config_file(config_path)

    def _send_radar_config_file(self, config_path, micro_doppler_only=None):
        try:
            profile_shape = parse_radar_profile_shape(config_path)
            if micro_doppler_only is None:
                required_virtual_antennas = max(
                    self.base_config.dsp.azimuth_channels
                    + self.base_config.dsp.elevation_channels
                ) + 1
                micro_doppler_only = (
                    "micro_doppler" in os.path.basename(config_path).lower()
                    or profile_shape.tx_antennas * profile_shape.rx_antennas
                    < required_virtual_antennas
                )
            self._apply_radar_profile(
                profile_shape,
                micro_doppler_only=micro_doppler_only,
            )
            self.open_radar(config_path, self.cli_port_name)
        except Exception as error:
            self.print_log("发送失败: {}".format(error), "red")
            return
        self.print_log(
            "发送成功；已按配置使用 ADC/chirp/TX/RX={}，帧长度={} int16".format(
                profile_shape.as_tuple(), self.config.radar.raw_values_per_frame
            ),
            "green",
        )

    def _apply_radar_profile(self, profile_shape, micro_doppler_only=False):
        # Dedicated profiles can use the stock 3-TX/4-RX OOB point-cloud
        # configuration while the host micro-Doppler path consumes only the
        # TX0-RX0 virtual channel.
        runtime_config = self.base_config.with_radar_shape(
            *profile_shape.as_tuple(),
            micro_doppler_only=micro_doppler_only,
        )
        if runtime_config == self.config:
            if self.ui is not None and hasattr(self.ui, "fullFeatureAction"):
                self._update_display_mode_availability()
            return

        feature_queue = Queue(maxsize=runtime_config.feature_queue_size)
        signal_processor = RadarSignalProcessor(
            runtime_config, self.runtime_state
        )

        # Native capture owns a frame-sized double buffer. Stop the previous
        # capture process before replacing it with the selected cfg's exact
        # frame length.
        self._stop_hardware()
        self.config = runtime_config
        if self.ui is not None:
            self.ui.config = runtime_config
        self.feature_queue = feature_queue
        self.signal_processor = signal_processor
        self.latest_features = None
        if self.ui is not None and hasattr(self.ui, "fullFeatureAction"):
            self._update_display_mode_availability()

    def open_radar(self, config_path, com_port):
        self._ensure_capture_backend()

        if self.radar_ctrl is not None:
            try:
                self.radar_ctrl.disconnect()
            finally:
                self.radar_ctrl = None

        radar_ctrl = RadarCliClient(
            name="ConnectRadar",
            cli_port=com_port,
            settings=self.config.serial,
            log_callback=self.print_log,
        )
        try:
            radar_ctrl.send_config(config_path)
        except Exception:
            try:
                radar_ctrl.disconnect()
            except Exception:
                # disconnect() always closes the serial port in its finally
                # block. Keep the original configuration error for the UI.
                pass
            raise
        self.radar_ctrl = radar_ctrl

        if self.processor.ident is None:
            self.processor.start()
        elif not self.processor.is_alive():
            self.processor = DataProcessor(
                "Processor",
                self.capture_buffer,
                self.signal_processor,
                self.feature_queue,
                self.config.radar,
            )
            self.processor.start()

    def set_color(self):
        color_name = self.ui.colorMapCombo.currentText()
        if color_name in ("", "--select--"):
            return
        if color_name == "customize":
            color_map = pg_get_cmap(color_name)
        else:
            color_map = pg_get_cmap(get_matplotlib_colormap(color_name))
        lookup_table = color_map.getLookupTable(0.0, 1.0, 256)
        for image in self.images.values():
            image.setLookupTable(lookup_table)

    def print_log(self, message, color="green"):
        self.ui.logTextEdit.moveCursor(QtGui.QTextCursor.End)
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        self.ui.logTextEdit.append(
            '<font color="{}">{}-->{}</font>'.format(
                html.escape(color), timestamp, html.escape(str(message))
            )
        )

    def shutdown(self):
        self._stop_hardware()

    def _stop_hardware(self):
        if self.radar_ctrl is not None:
            try:
                if self.radar_ctrl.is_open:
                    self.radar_ctrl.disconnect()
            except Exception:
                pass
            finally:
                self.radar_ctrl = None
        if self.processor is not None:
            self.processor.stop()
        if self.dca1000 is not None:
            try:
                self.dca1000.close()
            except Exception:
                pass
            finally:
                self.dca1000 = None
        if self.processor is not None and self.processor.ident is not None:
            self.processor.join(timeout=1)
        self.processor = None
        if self.collector is not None:
            if self.collector.ident is not None:
                self.collector.stop()
                self.collector.join(timeout=1)
        self.collector = None
        self.capture_buffer = None


def main():
    return RadarStreamApplication().run()


if __name__ == "__main__":
    sys.exit(main())
