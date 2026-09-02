"""Compact PyQt5 widget for selecting or generating radar configurations."""

import math
from pathlib import Path

from PyQt5 import QtCore, QtGui, QtWidgets

from .engine import ConfigValidationError, Iwr6843ConfigEngine


PARAMETER_SPECS = (
    ("start_frequency_ghz", "起始频率", 55.0, 82.0, 0.01, "GHz"),
    ("idle_time_us", "Idle Time", 0.1, 1000.0, 0.1, "us"),
    ("adc_start_time_us", "ADC Start", 0.0, 200.0, 0.1, "us"),
    ("ramp_end_time_us", "Ramp End", 0.1, 1000.0, 0.1, "us"),
    ("frequency_slope_mhz_us", "频率斜率", 0.1, 399.0, 0.1, "MHz/us"),
    ("adc_samples", "ADC 采样点", 2.0, 2048.0, 1.0, "点"),
    ("sample_rate_ksps", "采样率", 100.0, 25000.0, 1.0, "ksps"),
    ("chirp_loops", "Chirp Loops", 1.0, 4096.0, 1.0, "次"),
    ("frame_period_ms", "Frame Period", 0.1, 10000.0, 0.1, "ms"),
)


class RefreshComboBox(QtWidgets.QComboBox):
    """Combo box that announces when its popup is about to be displayed."""

    popupAboutToShow = QtCore.pyqtSignal()

    def showPopup(self):
        self.popupAboutToShow.emit()
        super().showPopup()


class ConstraintSlider(QtWidgets.QSlider):
    """A compact slider that visualizes the currently valid value interval."""

    HANDLE_RADIUS = 7
    TRACK_MARGIN = 9
    TRACK_Y = 9

    def __init__(self, minimum, maximum, step, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.physical_minimum = float(minimum)
        self.physical_maximum = float(maximum)
        self.step = float(step)
        self.valid_minimum = self.physical_minimum
        self.valid_maximum = self.physical_maximum
        self._invalid = False
        self.setRange(
            0,
            int(round(
                (self.physical_maximum - self.physical_minimum) / self.step
            )),
        )
        self.setMinimumHeight(34)
        self.setMaximumHeight(38)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def sizeHint(self):
        return QtCore.QSize(190, 36)

    def physical_value(self):
        return self.physical_minimum + self.value() * self.step

    def set_valid_range(self, minimum, maximum):
        self.valid_minimum = float(minimum)
        self.valid_maximum = float(maximum)
        if self.valid_minimum <= self.valid_maximum:
            self.setToolTip(
                "当前有效范围：{} ～ {}".format(
                    self._format_value(self.valid_minimum),
                    self._format_value(self.valid_maximum),
                )
            )
        else:
            self.setToolTip("当前参数组合不存在可行区间")
        self.update()

    def is_value_valid(self, value=None):
        if value is None:
            value = self.physical_value()
        tolerance = max(abs(self.step) * 1e-6, 1e-9)
        return (
            self.valid_minimum <= self.valid_maximum
            and value >= self.valid_minimum - tolerance
            and value <= self.valid_maximum + tolerance
        )

    def set_invalid(self, invalid):
        self._invalid = bool(invalid)
        self.update()

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        left = float(self.TRACK_MARGIN)
        right = float(max(self.TRACK_MARGIN, self.width() - self.TRACK_MARGIN))
        track_y = float(self.TRACK_Y)

        painter.setPen(
            QtGui.QPen(
                QtGui.QColor("#bfd6e8"),
                4,
                QtCore.Qt.SolidLine,
                QtCore.Qt.RoundCap,
            )
        )
        painter.drawLine(QtCore.QPointF(left, track_y), QtCore.QPointF(right, track_y))

        valid_min_x = self._value_to_x(self.valid_minimum, left, right)
        valid_max_x = self._value_to_x(self.valid_maximum, left, right)
        painter.setPen(
            QtGui.QPen(
                QtGui.QColor("#f3a8ae"),
                4,
                QtCore.Qt.SolidLine,
                QtCore.Qt.RoundCap,
            )
        )
        if self.valid_minimum > self.physical_minimum:
            painter.drawLine(
                QtCore.QPointF(left, track_y),
                QtCore.QPointF(valid_min_x, track_y),
            )
        if self.valid_maximum < self.physical_maximum:
            painter.drawLine(
                QtCore.QPointF(valid_max_x, track_y),
                QtCore.QPointF(right, track_y),
            )
        if self.valid_minimum > self.valid_maximum:
            painter.drawLine(
                QtCore.QPointF(left, track_y),
                QtCore.QPointF(right, track_y),
            )

        markers = self._constraint_markers(left, right)
        painter.setPen(QtGui.QPen(QtGui.QColor("#202124"), 4))
        for _, marker_x in markers:
            painter.drawLine(
                QtCore.QPointF(marker_x, 2.0),
                QtCore.QPointF(marker_x, 17.0),
            )

        value_x = self._value_to_x(self.physical_value(), left, right)
        handle_color = "#ff7b82" if self._invalid else "#1479bd"
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(handle_color))
        painter.drawEllipse(
            QtCore.QPointF(value_x, track_y),
            self.HANDLE_RADIUS,
            self.HANDLE_RADIUS,
        )

        label_font = QtGui.QFont(self.font())
        if label_font.pointSizeF() > 0:
            label_font.setPointSizeF(max(7.0, label_font.pointSizeF() - 1.0))
        painter.setFont(label_font)
        painter.setPen(QtGui.QColor("#475467"))
        label_y = 20
        label_height = max(12, self.height() - label_y)
        marker_to_label = self._labelled_marker(markers)
        marker_x = marker_to_label[1] if marker_to_label else None
        edge_clearance = 34
        if marker_x is None or marker_x - left > edge_clearance:
            painter.drawText(
                QtCore.QRectF(0, label_y, self.width() / 2.0, label_height),
                QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
                self._format_value(self.physical_minimum),
            )
        if marker_x is None or right - marker_x > edge_clearance:
            painter.drawText(
                QtCore.QRectF(
                    self.width() / 2.0,
                    label_y,
                    self.width() / 2.0,
                    label_height,
                ),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                self._format_value(self.physical_maximum),
            )
        if marker_to_label:
            marker_value, marker_x = marker_to_label
            label_width = 58.0
            painter.drawText(
                QtCore.QRectF(
                    marker_x - label_width / 2.0,
                    label_y,
                    label_width,
                    label_height,
                ),
                QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter,
                self._format_value(marker_value),
            )

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.setFocus(QtCore.Qt.MouseFocusReason)
            self.setSliderDown(True)
            self._set_value_from_x(event.x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown() and event.buttons() & QtCore.Qt.LeftButton:
            self._set_value_from_x(event.x())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.isSliderDown():
            self._set_value_from_x(event.x())
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_value_from_x(self, position_x):
        span = max(1, self.width() - 2 * self.TRACK_MARGIN)
        position = max(0, min(span, int(position_x) - self.TRACK_MARGIN))
        self.setValue(
            QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), position, span
            )
        )

    def _value_to_x(self, value, left, right):
        if not math.isfinite(float(value)):
            value = (
                self.physical_maximum
                if value > 0
                else self.physical_minimum
            )
        value = max(self.physical_minimum, min(self.physical_maximum, value))
        span = self.physical_maximum - self.physical_minimum
        if span <= 0:
            return left
        return left + (right - left) * (value - self.physical_minimum) / span

    def _constraint_markers(self, left, right):
        tolerance = max(abs(self.step) * 1e-6, 1e-9)
        markers = []
        if self.valid_minimum > self.physical_minimum + tolerance:
            markers.append(
                (self.valid_minimum, self._value_to_x(self.valid_minimum, left, right))
            )
        if self.valid_maximum < self.physical_maximum - tolerance:
            marker = (
                self.valid_maximum,
                self._value_to_x(self.valid_maximum, left, right),
            )
            if not markers or abs(markers[-1][1] - marker[1]) > 2:
                markers.append(marker)
        return markers

    def _labelled_marker(self, markers):
        if not markers:
            return None
        value = self.physical_value()
        if value < self.valid_minimum:
            return markers[0]
        if value > self.valid_maximum:
            return markers[-1]
        return min(markers, key=lambda marker: abs(marker[0] - value))

    def _format_value(self, value):
        if not math.isfinite(float(value)):
            return "∞" if value > 0 else "-∞"
        decimals = ParameterControl._decimals(self.step)
        text = ("{:.%df}" % decimals).format(value)
        return text.rstrip("0").rstrip(".") if "." in text else text


class ParameterControl(QtWidgets.QWidget):
    """A synchronized slider and numeric editor for one radar field."""

    valueChanged = QtCore.pyqtSignal(str, float)

    def __init__(self, name, label, minimum, maximum, step, unit, parent=None):
        super().__init__(parent)
        self.name = name
        self.minimum = float(minimum)
        self.maximum = float(maximum)
        self.step = float(step)
        self._updating = False

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(6)
        self.label = QtWidgets.QLabel(label + "：")
        self.label.setToolTip(name)
        self.label.setFixedWidth(84)
        self.label.setAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        layout.addWidget(self.label)

        self.slider = ConstraintSlider(
            self.minimum, self.maximum, self.step
        )
        self.slider.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        layout.addWidget(self.slider, 1)

        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(self.minimum, self.maximum)
        self.spin.setSingleStep(self.step)
        self.spin.setDecimals(self._decimals(self.step))
        self.spin.setSuffix(" " + unit if unit else "")
        self.spin.setKeyboardTracking(False)
        self.spin.setMinimumWidth(108)
        self.spin.setMaximumWidth(122)
        self.spin.setStyleSheet(self._spin_style(False))
        layout.addWidget(self.spin)

        self.slider.valueChanged.connect(self._slider_changed)
        self.spin.valueChanged.connect(self._spin_changed)

    @staticmethod
    def _decimals(step):
        if step >= 1:
            return 0
        if step >= 0.1:
            return 1
        return 2

    def value(self):
        return self.spin.value()

    def set_value(self, value):
        value = max(self.minimum, min(self.maximum, float(value)))
        self._updating = True
        self.spin.setValue(value)
        self.slider.setValue(int(round((value - self.minimum) / self.step)))
        self._updating = False
        self.set_invalid(not self.slider.is_value_valid(value))

    def set_constraints(self, minimum, maximum):
        self.slider.set_valid_range(minimum, maximum)
        self.set_invalid(not self.slider.is_value_valid(self.value()))

    def set_invalid(self, invalid):
        self.slider.set_invalid(invalid)
        self.spin.setStyleSheet(self._spin_style(invalid))

    @staticmethod
    def _spin_style(invalid):
        background = "#ff7b82" if invalid else "#ffffff"
        return (
            "QDoubleSpinBox {"
            " background-color: %s;"
            " color: #101828;"
            " border: 1px solid #202124;"
            " padding: 1px 3px;"
            "}"
        ) % background

    def _slider_changed(self, position):
        if self._updating:
            return
        value = self.minimum + position * self.step
        self._updating = True
        self.spin.setValue(value)
        self._updating = False
        self.set_invalid(not self.slider.is_value_valid(value))
        self.valueChanged.emit(self.name, value)

    def _spin_changed(self, value):
        if self._updating:
            return
        self._updating = True
        self.slider.setValue(int(round((value - self.minimum) / self.step)))
        self._updating = False
        self.set_invalid(not self.slider.is_value_valid(value))
        self.valueChanged.emit(self.name, value)


class RadarConfigPanel(QtWidgets.QWidget):
    """Dock-ready component for cfg selection and generation."""

    sendRequested = QtCore.pyqtSignal()
    exitRequested = QtCore.pyqtSignal()
    configSelectionChanged = QtCore.pyqtSignal(str)
    configSaved = QtCore.pyqtSignal(str)

    EXISTING_SOURCE = "existing"
    GENERATED_SOURCE = "generated"

    def __init__(self, template_path, config_dir, parent=None):
        super().__init__(parent)
        self.template_path = Path(template_path)
        self.config_dir = Path(config_dir)
        self.engine = Iwr6843ConfigEngine(self.template_path)
        self.values = self.engine.default_values()
        self._updating = False
        self._config_load_error = ""
        self.parameter_controls = {}
        self.metric_labels = {}
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding,
            QtWidgets.QSizePolicy.Expanding,
        )
        self.setMinimumSize(330, 300)
        self._build_ui()
        self.refresh_config_files()
        self._refresh_display()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)

        source_row = QtWidgets.QGridLayout()
        source_row.setHorizontalSpacing(5)
        source_row.addWidget(QtWidgets.QLabel("来源："), 0, 0)
        self.source_combo = QtWidgets.QComboBox()
        self.source_combo.addItem("预设配置文件", self.EXISTING_SOURCE)
        self.source_combo.addItem("参数生成配置", self.GENERATED_SOURCE)
        source_row.addWidget(self.source_combo, 0, 1)
        source_row.addWidget(QtWidgets.QLabel("配置："), 1, 0)
        self.config_combo = RefreshComboBox()
        self.config_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToMinimumContentsLength)
        self.config_combo.setMinimumContentsLength(14)
        source_row.addWidget(self.config_combo, 1, 1)
        root.addLayout(source_row)

        channel_row = QtWidgets.QHBoxLayout()
        channel_row.setSpacing(3)
        self.rx_channel_checks = self._add_channel_checkboxes(
            channel_row, "RX", 4
        )
        channel_row.addSpacing(5)
        self.tx_channel_checks = self._add_channel_checkboxes(
            channel_row, "TX", 3
        )
        channel_row.addStretch(1)
        root.addLayout(channel_row)

        parameter_scroll = QtWidgets.QScrollArea()
        parameter_scroll.setWidgetResizable(True)
        parameter_scroll.setFrameShape(QtWidgets.QFrame.StyledPanel)
        parameter_scroll.setMinimumHeight(100)
        parameter_body = QtWidgets.QWidget()
        parameter_layout = QtWidgets.QVBoxLayout(parameter_body)
        parameter_layout.setContentsMargins(5, 3, 5, 3)
        parameter_layout.setSpacing(1)
        for spec in PARAMETER_SPECS:
            control = ParameterControl(*spec)
            control.valueChanged.connect(self._parameter_changed)
            self.parameter_controls[spec[0]] = control
            parameter_layout.addWidget(control)
        parameter_layout.addStretch(1)
        parameter_scroll.setWidget(parameter_body)
        root.addWidget(parameter_scroll, 1)

        metrics = QtWidgets.QGridLayout()
        metrics.setHorizontalSpacing(5)
        metric_specs = (
            ("range_resolution", "距离分辨率"),
            ("max_range", "最大距离"),
            ("velocity_resolution", "速度分辨率"),
            ("max_velocity", "最大速度"),
        )
        for index, (name, title) in enumerate(metric_specs):
            row = index // 2
            column = (index % 2) * 2
            metrics.addWidget(QtWidgets.QLabel(title + "："), row, column)
            value = QtWidgets.QLabel("—")
            value.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            metrics.addWidget(value, row, column + 1)
            self.metric_labels[name] = value
        root.addLayout(metrics)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setMaximumHeight(42)
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        root.addWidget(self.status_label)

        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(QtWidgets.QLabel("CLI Port："))
        self.cli_combo = RefreshComboBox()
        port_row.addWidget(self.cli_combo, 1)
        root.addLayout(port_row)

        action_grid = QtWidgets.QGridLayout()
        action_grid.setSpacing(4)
        self.preview_button = QtWidgets.QPushButton("预览")
        self.save_button = QtWidgets.QPushButton("保存配置")
        self.send_button = QtWidgets.QPushButton("发送配置")
        self.exit_button = QtWidgets.QPushButton("退出")
        action_grid.addWidget(self.preview_button, 0, 0)
        action_grid.addWidget(self.save_button, 0, 1)
        action_grid.addWidget(self.exit_button, 0, 2)
        action_grid.addWidget(self.send_button, 0, 3)
        root.addLayout(action_grid)

        self.source_combo.currentIndexChanged.connect(self._source_changed)
        self.config_combo.currentIndexChanged.connect(self._selected_file_changed)
        self.config_combo.popupAboutToShow.connect(self.refresh_config_files)
        for checkbox in self.rx_channel_checks + self.tx_channel_checks:
            checkbox.toggled.connect(self._channel_changed)
        self.preview_button.clicked.connect(self.show_preview)
        self.save_button.clicked.connect(self.save_config)
        self.send_button.clicked.connect(self.sendRequested.emit)
        self.exit_button.clicked.connect(self.exitRequested.emit)

    def source(self):
        return self.source_combo.currentData()

    def uses_generated_config(self):
        return self.source() == self.GENERATED_SOURCE

    def selected_config_path(self):
        value = self.config_combo.currentData()
        return str(value) if value else ""

    def selected_cli_port(self):
        return self.cli_combo.currentText().strip()

    def current_config_text(self):
        if self.uses_generated_config():
            return self.engine.render(self.values)
        path = self.selected_config_path()
        if not path:
            raise ValueError("请先选择配置文件")
        return Path(path).read_text(encoding="utf-8")

    def generated_config_text(self):
        return self.engine.render(self.values)

    def generated_is_micro_doppler_only(self):
        metrics = self.engine.metrics(self.values)
        return metrics.rx_antennas * metrics.tx_antennas < 12

    def refresh_config_files(self):
        previous = self.selected_config_path()
        paths = sorted(self.config_dir.glob("*.cfg"), key=lambda path: path.name.lower())
        self._updating = True
        self.config_combo.clear()
        for path in paths:
            self.config_combo.addItem(path.name, str(path.resolve()))
        preferred = previous or str(self.template_path.resolve())
        index = self.config_combo.findData(preferred)
        if index < 0 and self.config_combo.count():
            index = 0
        self.config_combo.setCurrentIndex(index)
        self._updating = False
        if not self.uses_generated_config() or not previous:
            self._selected_file_changed(index)

    def set_cli_ports(self, ports, selected=""):
        previous = selected or self.selected_cli_port()
        self.cli_combo.blockSignals(True)
        self.cli_combo.clear()
        for port in ports:
            device = getattr(port, "device", str(port))
            description = getattr(port, "description", "")
            self.cli_combo.addItem(device)
            self.cli_combo.setItemData(
                self.cli_combo.count() - 1,
                description,
                QtCore.Qt.ToolTipRole,
            )
        index = self.cli_combo.findText(previous)
        self.cli_combo.setCurrentIndex(index)
        self.cli_combo.blockSignals(False)

    def select_config_path(self, config_path):
        target = str(Path(config_path).resolve())
        index = self.config_combo.findData(target)
        if index >= 0:
            self.config_combo.setCurrentIndex(index)
        return index >= 0

    def save_config(self, target_path=None):
        try:
            text = self.generated_config_text()
        except ConfigValidationError as error:
            QtWidgets.QMessageBox.warning(self, "配置无效", str(error))
            return ""
        if not target_path:
            suffix = (
                "_micro_doppler"
                if self.generated_is_micro_doppler_only()
                else ""
            )
            suggested = str(
                self.config_dir / "iwr6843_generated{}.cfg".format(suffix)
            )
            target_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "保存雷达配置",
                suggested,
                "Radar config (*.cfg);;All files (*)",
            )
        if not target_path:
            return ""
        target = Path(target_path)
        if target.suffix.lower() != ".cfg":
            target = target.with_suffix(".cfg")
        try:
            with target.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
        except OSError as error:
            QtWidgets.QMessageBox.critical(self, "保存失败", str(error))
            return ""
        self.refresh_config_files()
        self.select_config_path(target)
        self.configSaved.emit(str(target))
        return str(target)

    def show_preview(self):
        try:
            text = self.current_config_text()
        except (OSError, ValueError) as error:
            QtWidgets.QMessageBox.warning(self, "无法预览", str(error))
            return
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("雷达配置预览")
        dialog.resize(720, 560)
        layout = QtWidgets.QVBoxLayout(dialog)
        editor = QtWidgets.QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        editor.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        editor.setPlainText(text)
        layout.addWidget(editor)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec_()

    def _source_changed(self):
        if self.uses_generated_config():
            self._refresh_display()
        else:
            self._selected_file_changed(self.config_combo.currentIndex())

    def _selected_file_changed(self, index):
        if self._updating or index < 0:
            return
        path = self.selected_config_path()
        self.config_combo.setToolTip(path)
        try:
            self.values = self.engine.parse_file(path)
            self._config_load_error = ""
            self._refresh_display()
        except (OSError, ValueError) as error:
            self._config_load_error = str(error)
            self.status_label.setStyleSheet("color: #b42318;")
            self.status_label.setText("配置解析失败：{}".format(error))
            self.send_button.setEnabled(False)
            self.save_button.setEnabled(False)
        self.configSelectionChanged.emit(path)

    def _parameter_changed(self, name, value):
        if self._updating:
            return
        self.values = self.engine.with_value(self.values, name, value)
        if not self.uses_generated_config():
            self.source_combo.setCurrentIndex(
                self.source_combo.findData(self.GENERATED_SOURCE)
            )
        self._refresh_display()

    @staticmethod
    def _add_channel_checkboxes(layout, prefix, count):
        checkboxes = []
        for index in range(count):
            checkbox = QtWidgets.QCheckBox("{}{}".format(prefix, index + 1))
            checkbox.setToolTip(
                "{} 通道，mask 位 {}".format(prefix, index)
            )
            layout.addWidget(checkbox)
            checkboxes.append(checkbox)
        return checkboxes

    @staticmethod
    def _checkbox_mask(checkboxes):
        return sum(
            1 << index
            for index, checkbox in enumerate(checkboxes)
            if checkbox.isChecked()
        )

    @staticmethod
    def _set_checkbox_mask(checkboxes, mask):
        for index, checkbox in enumerate(checkboxes):
            checkbox.setChecked(bool(mask & (1 << index)))

    @staticmethod
    def _set_channel_invalid(checkboxes, invalid):
        style = "QCheckBox { color: #b42318; }" if invalid else ""
        for checkbox in checkboxes:
            checkbox.setStyleSheet(style)

    def _channel_changed(self, checked=False):
        del checked
        if self._updating:
            return
        rx_mask = self._checkbox_mask(self.rx_channel_checks)
        tx_mask = self._checkbox_mask(self.tx_channel_checks)
        self.values = self.engine.with_value(
            self.values, "rx_channel_mask", rx_mask
        )
        self.values = self.engine.with_value(
            self.values, "tx_channel_mask", tx_mask
        )
        if not self.uses_generated_config():
            self.source_combo.setCurrentIndex(
                self.source_combo.findData(self.GENERATED_SOURCE)
            )
        self._refresh_display()

    def _refresh_display(self):
        self._updating = True
        constraints = self.engine.parameter_constraints(self.values)
        for name, control in self.parameter_controls.items():
            control.set_value(getattr(self.values, name))
            control.set_constraints(*constraints[name])
        self._set_checkbox_mask(
            self.rx_channel_checks, self.values.rx_channel_mask
        )
        self._set_checkbox_mask(
            self.tx_channel_checks, self.values.tx_channel_mask
        )
        self._updating = False

        metrics = self.engine.metrics(self.values)
        self.metric_labels["range_resolution"].setText(
            "{:.3f} m".format(metrics.range_resolution_m)
        )
        self.metric_labels["max_range"].setText(
            "{:.2f} m".format(metrics.max_range_m)
        )
        self.metric_labels["velocity_resolution"].setText(
            "{:.3f} m/s".format(metrics.velocity_resolution_mps)
        )
        self.metric_labels["max_velocity"].setText(
            "{:.2f} m/s".format(metrics.max_velocity_mps)
        )

        issues = self.engine.validate(self.values)
        self._set_channel_invalid(
            self.rx_channel_checks, self.values.rx_channel_mask == 0
        )
        self._set_channel_invalid(
            self.tx_channel_checks, self.values.tx_channel_mask == 0
        )
        if self._config_load_error and not self.uses_generated_config():
            self.status_label.setStyleSheet("color: #b42318;")
            self.status_label.setText(
                "配置解析失败：{}".format(self._config_load_error)
            )
        elif issues:
            self.status_label.setStyleSheet("color: #b42318;")
            self.status_label.setText("参数校验：" + "；".join(issues))
        elif self.uses_generated_config():
            self.status_label.setStyleSheet("color: #067647;")
            self.status_label.setText("参数有效，将生成临时配置后发送")
        else:
            self.status_label.setStyleSheet("color: #475467;")
            self.status_label.setText("发送所选文件；修改参数后自动切换为生成模式")
        if self.uses_generated_config():
            self.send_button.setEnabled(not issues)
        else:
            self.send_button.setEnabled(
                bool(self.selected_config_path()) and not self._config_load_error
            )
        self.save_button.setEnabled(self.uses_generated_config() and not issues)
