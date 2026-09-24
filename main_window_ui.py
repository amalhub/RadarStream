"""Main-window widgets for the dock-based RadarStream interface."""

import pyqtgraph as pg
from PyQt5 import QtCore, QtGui, QtWidgets
from pyqtgraph import GraphicsLayoutWidget

from app_config import DEFAULT_CONFIG
from colormap_utils import matplotlib_colormap_names


class DockGuideOverlay(QtWidgets.QWidget):
    """Visual docking guide layered over the main window during a drag."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.dragged_dock = None
        self.target_area = QtCore.Qt.NoDockWidgetArea
        self.setObjectName("dockGuideOverlay")
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.hide()

        # A release from a native Windows title-bar drag is not guaranteed to
        # return to QDockWidget.mouseReleaseEvent. Polling the global mouse
        # state keeps the guide and final drop behavior reliable in that case.
        self.drag_timer = QtCore.QTimer(self)
        self.drag_timer.setInterval(40)
        self.drag_timer.timeout.connect(self._poll_drag)

    def begin_drag(self, dock):
        self.dragged_dock = dock
        self.target_area = QtCore.Qt.NoDockWidgetArea
        self.setGeometry(self.main_window.rect())
        self.update_drag(QtGui.QCursor.pos())
        self.drag_timer.start()

    def update_drag(self, global_position):
        if self.dragged_dock is None:
            return
        self.setGeometry(self.main_window.rect())
        local_position = self.mapFromGlobal(global_position)
        if not self.rect().contains(local_position):
            self.target_area = QtCore.Qt.NoDockWidgetArea
            self.hide()
            return
        self.target_area = self._area_at(local_position)
        self.show()
        self.raise_()
        self.update()

    def finish_drag(self, global_position=None):
        dock = self.dragged_dock
        if dock is None:
            return
        if global_position is not None:
            self.update_drag(global_position)
        target_area = self.target_area
        self.dragged_dock = None
        self.target_area = QtCore.Qt.NoDockWidgetArea
        self.drag_timer.stop()
        self.hide()
        if (
            target_area != QtCore.Qt.NoDockWidgetArea
            and dock.allowedAreas() & target_area
        ):
            QtCore.QTimer.singleShot(
                0, lambda: self._dock_widget(dock, target_area)
            )

    def guide_rects(self):
        """Return the four compass buttons in overlay-local coordinates."""

        content = self._content_rect()
        center = content.center()
        size = 42
        offset = 46
        return {
            QtCore.Qt.LeftDockWidgetArea: QtCore.QRect(
                center.x() - offset - size // 2,
                center.y() - size // 2,
                size,
                size,
            ),
            QtCore.Qt.RightDockWidgetArea: QtCore.QRect(
                center.x() + offset - size // 2,
                center.y() - size // 2,
                size,
                size,
            ),
            QtCore.Qt.TopDockWidgetArea: QtCore.QRect(
                center.x() - size // 2,
                center.y() - offset - size // 2,
                size,
                size,
            ),
            QtCore.Qt.BottomDockWidgetArea: QtCore.QRect(
                center.x() - size // 2,
                center.y() + offset - size // 2,
                size,
                size,
            ),
        }

    def paintEvent(self, event):
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        if self.target_area != QtCore.Qt.NoDockWidgetArea:
            preview = self._preview_rect(self.target_area)
            painter.setPen(QtGui.QPen(QtGui.QColor(50, 82, 105, 225), 2))
            painter.setBrush(QtGui.QColor(76, 91, 104, 82))
            painter.drawRect(preview)

        for area, rect in self.guide_rects().items():
            active = area == self.target_area
            border = QtGui.QColor("#334e62" if active else "#788895")
            fill = QtGui.QColor("#b8c2ca" if active else "#eef2f4")
            painter.setPen(QtGui.QPen(border, 2 if active else 1))
            painter.setBrush(fill)
            painter.drawRoundedRect(rect, 4, 4)
            self._draw_area_icon(
                painter,
                rect.adjusted(9, 9, -9, -9),
                area,
                active,
            )

    def _draw_area_icon(self, painter, rect, area, active):
        outline = "#354753" if active else "#72828e"
        background = "#e8ecef" if active else "#ffffff"
        direction_fill = "#46545f" if active else "#aeb9c1"
        painter.setPen(QtGui.QPen(QtGui.QColor(outline), 1))
        painter.setBrush(QtGui.QColor(background))
        painter.drawRect(rect)
        painter.setBrush(QtGui.QColor(direction_fill))
        if area == QtCore.Qt.LeftDockWidgetArea:
            area_rect = QtCore.QRect(rect.left(), rect.top(), rect.width() // 2, rect.height())
        elif area == QtCore.Qt.RightDockWidgetArea:
            area_rect = QtCore.QRect(
                rect.center().x(), rect.top(), rect.width() // 2 + 1, rect.height()
            )
        elif area == QtCore.Qt.TopDockWidgetArea:
            area_rect = QtCore.QRect(rect.left(), rect.top(), rect.width(), rect.height() // 2)
        else:
            area_rect = QtCore.QRect(
                rect.left(), rect.center().y(), rect.width(), rect.height() // 2 + 1
            )
        painter.drawRect(area_rect)

    def _area_at(self, position):
        for area, rect in self.guide_rects().items():
            if rect.contains(position):
                return area

        content = self._content_rect()
        if not content.contains(position):
            return QtCore.Qt.NoDockWidgetArea
        margin = max(48, min(content.width(), content.height()) // 8)
        distances = {
            QtCore.Qt.LeftDockWidgetArea: position.x() - content.left(),
            QtCore.Qt.RightDockWidgetArea: content.right() - position.x(),
            QtCore.Qt.TopDockWidgetArea: position.y() - content.top(),
            QtCore.Qt.BottomDockWidgetArea: content.bottom() - position.y(),
        }
        area, distance = min(distances.items(), key=lambda item: item[1])
        return area if distance <= margin else QtCore.Qt.NoDockWidgetArea

    def _content_rect(self):
        top = self.main_window.menuBar().height() if self.main_window.menuBar() else 0
        bottom = (
            self.main_window.statusBar().height()
            if self.main_window.statusBar() is not None
            else 0
        )
        return self.rect().adjusted(8, top + 8, -8, -bottom - 8)

    def _preview_rect(self, area):
        content = self._content_rect()
        if area == QtCore.Qt.LeftDockWidgetArea:
            content.setWidth(content.width() // 2)
        elif area == QtCore.Qt.RightDockWidgetArea:
            half = content.width() // 2
            content.setLeft(content.right() - half)
        elif area == QtCore.Qt.TopDockWidgetArea:
            content.setHeight(content.height() // 2)
        elif area == QtCore.Qt.BottomDockWidgetArea:
            half = content.height() // 2
            content.setTop(content.bottom() - half)
        return content

    def _poll_drag(self):
        if not QtWidgets.QApplication.mouseButtons() & QtCore.Qt.LeftButton:
            self.finish_drag(QtGui.QCursor.pos())
        else:
            self.update_drag(QtGui.QCursor.pos())

    def _dock_widget(self, dock, area):
        dock._last_guide_target_area = area
        self.main_window.removeDockWidget(dock)
        self.main_window.addDockWidget(area, dock)
        dock.setFloating(False)
        dock.show()


class DockTitleBar(QtWidgets.QFrame):
    """Custom title bar that makes guided dock dragging deterministic."""

    def __init__(self, dock, guide_overlay):
        super().__init__(dock)
        self.dock = dock
        self.guide_overlay = guide_overlay
        self._press_position = None
        self._dock_offset = None
        self._dragging = False
        self.setObjectName("dockTitleBar")
        self.setFixedHeight(25)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setStyleSheet(
            "QFrame#dockTitleBar {"
            "  background: #d8dde2;"
            "  border: 1px solid #7b858f;"
            "}"
            "QLabel { border: 0; background: transparent; }"
            "QToolButton { border: 0; padding: 1px; }"
            "QToolButton:hover { background: #c3cbd3; }"
        )

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(5, 1, 3, 1)
        layout.setSpacing(2)
        self.titleLabel = QtWidgets.QLabel(dock.windowTitle(), self)
        self.titleLabel.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.titleLabel, 1)

        self.floatButton = QtWidgets.QToolButton(self)
        self.floatButton.setObjectName("dockFloatButton")
        self.floatButton.setAutoRaise(True)
        self.floatButton.setFixedSize(18, 18)
        self.floatButton.clicked.connect(self._toggle_floating)
        layout.addWidget(self.floatButton)

        self.closeButton = QtWidgets.QToolButton(self)
        self.closeButton.setObjectName("dockCloseButton")
        self.closeButton.setAutoRaise(True)
        self.closeButton.setFixedSize(18, 18)
        self.closeButton.setIcon(
            self.style().standardIcon(QtWidgets.QStyle.SP_TitleBarCloseButton)
        )
        self.closeButton.setToolTip("Close")
        self.closeButton.clicked.connect(dock.close)
        layout.addWidget(self.closeButton)
        self.titleLayout = layout

        dock.windowTitleChanged.connect(self.titleLabel.setText)
        dock.topLevelChanged.connect(self._sync_float_button)
        self._sync_float_button(dock.isFloating())

    def add_action_button(self, text, callback, tooltip=""):
        """Add a dock-specific action before the standard title buttons."""

        button = QtWidgets.QToolButton(self)
        button.setText(text)
        button.setAutoRaise(True)
        button.setMinimumWidth(38)
        button.setFixedHeight(18)
        button.setToolTip(tooltip or text)
        button.clicked.connect(callback)
        self.titleLayout.insertWidget(self.titleLayout.count() - 2, button)
        return button

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._press_position = event.globalPos()
            self._dock_offset = self.dock.mapFromGlobal(event.globalPos())
            self._dragging = False
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_position is None
            or not event.buttons() & QtCore.Qt.LeftButton
        ):
            super().mouseMoveEvent(event)
            return

        distance = (event.globalPos() - self._press_position).manhattanLength()
        if not self._dragging and distance >= QtWidgets.QApplication.startDragDistance():
            self._start_drag(event.globalPos())
        if self._dragging:
            self.dock.move(event.globalPos() - self._dock_offset)
            self.guide_overlay.update_drag(event.globalPos())
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            if self._dragging:
                self.guide_overlay.finish_drag(event.globalPos())
            self._press_position = None
            self._dock_offset = None
            self._dragging = False
            self.setCursor(QtCore.Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self._toggle_floating()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _start_drag(self, global_position):
        if not self.dock.isFloating():
            docked_size = self.dock.size()
            self.dock.aboutToFloat.emit()
            self.dock.setFloating(True)
            self.dock.resize(docked_size)
        self._dragging = True
        self.dock.move(global_position - self._dock_offset)
        self.guide_overlay.begin_drag(self.dock)

    def _toggle_floating(self):
        if self.dock.isFloating():
            self.dock.setFloating(False)
        else:
            self.dock.aboutToFloat.emit()
            self.dock.setFloating(True)

    def _sync_float_button(self, floating):
        icon_type = (
            QtWidgets.QStyle.SP_TitleBarNormalButton
            if floating
            else QtWidgets.QStyle.SP_TitleBarMaxButton
        )
        self.floatButton.setIcon(self.style().standardIcon(icon_type))
        self.floatButton.setToolTip("Dock" if floating else "Float")


class GuidedDockWidget(QtWidgets.QDockWidget):
    """Dock widget with a custom title bar and placement guide."""

    aboutToFloat = QtCore.pyqtSignal()

    def __init__(self, title, main_window, guide_overlay):
        super().__init__(title, main_window)
        self.guide_overlay = guide_overlay
        self.customTitleBar = DockTitleBar(self, guide_overlay)
        self.setTitleBarWidget(self.customTitleBar)


class Ui_MainWindow:
    """Build the four independent dock panels owned by the application."""

    def __init__(self, config=DEFAULT_CONFIG, runtime_state=None):
        self.config = config
        self.runtime_state = runtime_state

    def setupUi(self, main_window):
        self.mainWindow = main_window
        self._dock_return_states = {}
        main_window.setObjectName("MainWindow")
        main_window.resize(1280, 820)
        main_window.setWindowTitle("RadarStream")
        main_window.setDockNestingEnabled(True)
        main_window.setDockOptions(
            QtWidgets.QMainWindow.AnimatedDocks
            | QtWidgets.QMainWindow.AllowNestedDocks
            | QtWidgets.QMainWindow.AllowTabbedDocks
        )
        pg.setConfigOption("background", "#f0f0f0")
        pg.setConfigOption("foreground", "d")

        # QMainWindow requires a central widget. Keeping it at zero preferred
        # size lets the dock areas use the complete client region.
        self.centralWidget = QtWidgets.QWidget(main_window)
        self.centralWidget.setObjectName("centralWidget")
        central_policy = QtWidgets.QSizePolicy(
            QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored
        )
        self.centralWidget.setSizePolicy(central_policy)
        self.centralWidget.setMaximumSize(0, 0)
        main_window.setCentralWidget(self.centralWidget)

        self._build_menus(main_window)
        self.dockGuideOverlay = DockGuideOverlay(main_window)
        self._build_data_dock(main_window)
        self._build_config_dock(main_window)
        self._build_capture_dock(main_window)
        self._build_log_dock(main_window)
        self._arrange_docks(main_window)

        self.statusBar = QtWidgets.QStatusBar(main_window)
        self.statusBar.setObjectName("statusBar")
        main_window.setStatusBar(self.statusBar)
        QtCore.QMetaObject.connectSlotsByName(main_window)

    def _build_menus(self, main_window):
        self.menuBar = QtWidgets.QMenuBar(main_window)
        self.menuBar.setObjectName("menuBar")
        main_window.setMenuBar(self.menuBar)

        self.displayMenu = self.menuBar.addMenu("Display Mode")
        self.displayMenu.setObjectName("displayMenu")
        self.displayModeGroup = QtWidgets.QActionGroup(main_window)
        self.displayModeGroup.setObjectName("displayModeGroup")
        self.displayModeGroup.setExclusive(True)

        self.fullFeatureAction = QtWidgets.QAction(
            "Real-time system", main_window, checkable=True
        )
        self.fullFeatureAction.setObjectName("fullFeatureAction")
        self.fullFeatureAction.setData("full")
        self.fullFeatureAction.setChecked(True)
        self.microDopplerAction = QtWidgets.QAction(
            "Micro-Doppler", main_window, checkable=True
        )
        self.microDopplerAction.setObjectName("microDopplerAction")
        self.microDopplerAction.setData("micro_doppler")
        self.displayModeGroup.addAction(self.fullFeatureAction)
        self.displayModeGroup.addAction(self.microDopplerAction)
        self.displayMenu.addActions(self.displayModeGroup.actions())

        self.windowMenu = self.menuBar.addMenu("Window")
        self.windowMenu.setObjectName("windowMenu")

    def _new_dock(self, main_window, title, object_name):
        dock = GuidedDockWidget(title, main_window, self.dockGuideOverlay)
        dock.setObjectName(object_name)
        dock.setAllowedAreas(QtCore.Qt.AllDockWidgetAreas)
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFloatable
        )
        dock.setStyleSheet(
            "QDockWidget {"
            "  border: 0;"
            "}"
        )
        return dock

    @staticmethod
    def set_dock_content(dock, widget):
        """Wrap dock content in a platform-independent visible border."""

        previous_frame = dock.widget()
        frame = QtWidgets.QFrame(dock)
        frame.setObjectName(dock.objectName() + "ContentFrame")
        frame.setStyleSheet(
            "QFrame#{} {{"
            "  border: 1px solid #7b858f;"
            "  border-top: 0;"
            "  background-color: #f0f0f0;"
            "}}".format(frame.objectName())
        )
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(widget)
        dock.setWidget(frame)
        dock.contentFrame = frame
        dock.contentWidget = widget
        if previous_frame is not None and previous_frame is not frame:
            previous_frame.setParent(None)
            previous_frame.deleteLater()
        return frame

    def _build_data_dock(self, main_window):
        self.radarDataDock = self._new_dock(
            main_window, "Real-time Radar Data", "radarDataDock"
        )
        self.dataDisplayStack = QtWidgets.QStackedWidget(self.radarDataDock)
        self.dataDisplayStack.setObjectName("dataDisplayStack")

        self.fullFeaturePage = QtWidgets.QWidget()
        self.fullFeaturePage.setObjectName("fullFeaturePage")
        self.featureGrid = QtWidgets.QGridLayout(self.fullFeaturePage)
        self.featureGrid.setContentsMargins(8, 8, 8, 8)
        self.featureGrid.setHorizontalSpacing(8)
        self.featureGrid.setVerticalSpacing(8)

        self.rangeTimeView = self._add_feature_cell(
            "Range-Time", "rangeTimeView", 0, 0, 1, 1
        )
        self.dopplerTimeView = self._add_feature_cell(
            "Doppler-Time", "dopplerTimeView", 0, 1, 1, 1
        )
        self.rangeElevationView = self._add_feature_cell(
            "Range-Elevation", "rangeElevationView", 0, 2, 1, 1
        )
        self.rangeDopplerView = self._add_feature_cell(
            "Range-Doppler", "rangeDopplerView", 1, 0, 1, 1
        )
        self.rangeAzimuthView = self._add_feature_cell(
            "Range-Azimuth", "rangeAzimuthView", 1, 1, 1, 1
        )
        for column in range(3):
            self.featureGrid.setColumnStretch(column, 1)
        for row in range(2):
            self.featureGrid.setRowStretch(row, 1)

        self.microDopplerPage = QtWidgets.QWidget()
        self.microDopplerPage.setObjectName("microDopplerPage")
        micro_layout = QtWidgets.QVBoxLayout(self.microDopplerPage)
        micro_layout.setContentsMargins(12, 10, 12, 12)
        micro_title = QtWidgets.QLabel("Single-RX Micro-Doppler Feature")
        micro_title.setAlignment(QtCore.Qt.AlignCenter)
        micro_layout.addWidget(micro_title)
        self.microDopplerView = GraphicsLayoutWidget(self.microDopplerPage)
        self.microDopplerView.setObjectName("microDopplerView")
        self.microDopplerView.setMinimumSize(320, 240)
        self._style_feature_view(self.microDopplerView)
        micro_layout.addWidget(self.microDopplerView, 1)

        self.dataDisplayStack.addWidget(self.fullFeaturePage)
        self.dataDisplayStack.addWidget(self.microDopplerPage)
        self.set_dock_content(self.radarDataDock, self.dataDisplayStack)

    def _add_feature_cell(self, title, object_name, row, column, row_span, col_span):
        cell = QtWidgets.QWidget(self.fullFeaturePage)
        layout = QtWidgets.QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        label = QtWidgets.QLabel(title, cell)
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(label)
        view = GraphicsLayoutWidget(cell)
        view.setObjectName(object_name)
        view.setMinimumSize(150, 150)
        self._style_feature_view(view)
        view.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        layout.addWidget(view, 1)
        self.featureGrid.addWidget(cell, row, column, row_span, col_span)
        return view

    @staticmethod
    def _style_feature_view(view):
        """Keep an empty feature view visible before its first radar frame."""

        view.setBackground("#e5e7e9")
        view.setStyleSheet(
            "GraphicsLayoutWidget, GraphicsView {"
            "  border: 1px solid #a6adb4;"
            "  background-color: #e5e7e9;"
            "}"
        )

    def _build_config_dock(self, main_window):
        self.radarConfigDock = self._new_dock(
            main_window, "Radar Configuration", "radarConfigDock"
        )
        placeholder = QtWidgets.QWidget(self.radarConfigDock)
        placeholder.setObjectName("radarConfigPlaceholder")
        self.set_dock_content(self.radarConfigDock, placeholder)

    def _build_capture_dock(self, main_window):
        self.captureDock = self._new_dock(main_window, "Capture", "captureDock")
        body = QtWidgets.QWidget(self.captureDock)
        body.setObjectName("capturePanel")
        layout = QtWidgets.QFormLayout(body)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        self.colorMapCombo = QtWidgets.QComboBox(body)
        self.colorMapCombo.setObjectName("colorMapCombo")
        self.colorMapCombo.addItem("--select--")
        self.colorMapCombo.addItem("customize")
        self.colorMapCombo.addItems(matplotlib_colormap_names())
        layout.addRow("Colormap:", self.colorMapCombo)

        self.captureSubjectEdit = QtWidgets.QLineEdit("chaotic_dataset", body)
        self.captureSubjectEdit.setObjectName("captureSubjectEdit")
        layout.addRow("Dataset:", self.captureSubjectEdit)

        self.captureSceneCombo = QtWidgets.QComboBox(body)
        self.captureSceneCombo.setObjectName("captureSceneCombo")
        self.captureSceneCombo.addItems(
            ["Back", "Dblclick", "Down", "Front", "Left", "Right", "Up"]
        )
        layout.addRow("Scene:", self.captureSceneCombo)

        self.captureButton = QtWidgets.QPushButton("Start Capture", body)
        self.captureButton.setObjectName("captureButton")
        self.captureButton.setCheckable(True)
        layout.addRow(self.captureButton)
        self.set_dock_content(self.captureDock, body)

    def _build_log_dock(self, main_window):
        self.logDock = self._new_dock(main_window, "Log", "logDock")
        self.logTextEdit = QtWidgets.QTextEdit(self.logDock)
        self.logTextEdit.setObjectName("logTextEdit")
        self.logTextEdit.setReadOnly(True)
        self.logTextEdit.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.clearLogButton = self.logDock.customTitleBar.add_action_button(
            "Clear", self.logTextEdit.clear, "Clear log"
        )
        self.clearLogButton.setObjectName("clearLogButton")
        self.set_dock_content(self.logDock, self.logTextEdit)

    def _arrange_docks(self, main_window):
        main_window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, self.radarDataDock)
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.radarConfigDock)
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.captureDock)
        main_window.splitDockWidget(
            self.radarConfigDock, self.captureDock, QtCore.Qt.Vertical
        )
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, self.logDock)
        main_window.splitDockWidget(
            self.captureDock, self.logDock, QtCore.Qt.Vertical
        )

        docks = (
            self.radarDataDock,
            self.radarConfigDock,
            self.captureDock,
            self.logDock,
        )
        for dock in docks:
            self.windowMenu.addAction(dock.toggleViewAction())

        main_window.resizeDocks(
            [self.radarDataDock, self.radarConfigDock],
            [900, 380],
            QtCore.Qt.Horizontal,
        )
        main_window.resizeDocks(
            [self.radarConfigDock, self.captureDock, self.logDock],
            [500, 150, 150],
            QtCore.Qt.Vertical,
        )
        for dock in (
            self.radarDataDock,
            self.radarConfigDock,
            self.captureDock,
            self.logDock,
        ):
            dock.aboutToFloat.connect(
                lambda dock=dock: self._remember_dock_layout(dock)
            )
            dock.topLevelChanged.connect(
                lambda floating, dock=dock: self._dock_level_changed(
                    dock, floating
                )
            )

    def _remember_dock_layout(self, dock):
        """Remember the exact layout before a panel becomes floating."""

        dock._last_guide_target_area = QtCore.Qt.NoDockWidgetArea
        self._dock_return_states[dock.objectName()] = (
            self.mainWindow.saveState(),
            self.mainWindow.dockWidgetArea(dock),
        )

    def _dock_level_changed(self, dock, floating):
        if floating:
            return
        remembered = self._dock_return_states.pop(dock.objectName(), None)
        if remembered is None:
            return
        state, original_area = remembered
        target_area = getattr(
            dock, "_last_guide_target_area", QtCore.Qt.NoDockWidgetArea
        )
        dock._last_guide_target_area = QtCore.Qt.NoDockWidgetArea
        if target_area in (QtCore.Qt.NoDockWidgetArea, original_area):
            QtCore.QTimer.singleShot(
                0, lambda state=state: self.mainWindow.restoreState(state)
            )


if __name__ == "__main__":
    import sys

    app = QtWidgets.QApplication(sys.argv)
    window = QtWidgets.QMainWindow()
    Ui_MainWindow().setupUi(window)
    window.show()
    sys.exit(app.exec_())
