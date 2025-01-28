from PyQt6.QtWidgets import (QLabel, QApplication, QWidget, QFrame,
                            QSpinBox, QComboBox, QHBoxLayout, QVBoxLayout, QPushButton)
from PyQt6.QtCore import pyqtSlot as Slot
from .file_io import BrowseDirectory
import os

class ToggleButton(QPushButton):
    def __init__(self, paused_str, live_str):
        super().__init__(paused_str)
        self.paused_str = paused_str
        self.live_str = live_str
    def to_live_state(self, fn):
        self.toggle(self.live_str, fn)
    def to_paused_state(self, fn):
        self.toggle(self.paused_str, fn)
    def toggle(self, label, fn):
        self.setEnabled(False)
        self.setText(label)
        self.clicked.disconnect()
        self.clicked.connect(fn)
        self.setEnabled(True)


class QHLine(QFrame):
    def __init__(self):
        super(QHLine, self).__init__()
        self.setFrameShape(QFrame.Shape.HLine)
        self.setFrameShadow(QFrame.Shadow.Plain)

class SettingBoxWithDefaults(QHBoxLayout):
    def __init__(self, label, lower_limit, upper_limit, initial_val, defaults=["Custom"]):
        super().__init__()
        self.name = label
        self.label = QLabel(label)

        self.spinbox = QSpinBox()
        self.spinbox.setRange(lower_limit, upper_limit)
        self.spinbox.setSingleStep(1)
        self.spinbox.setValue(initial_val)
        self.spinbox.hide()

        self.dropdown = QComboBox()
        self.dropdown.addItems(defaults)
        self.dropdown.currentTextChanged.connect(self.process_input)
        self.dropdown.setCurrentText(str(initial_val))

        self.addWidget(self.label)
        self.addWidget(self.dropdown)
        self.addWidget(self.spinbox)

        self.last_custom_val = initial_val
    
    def getval(self) -> int:
        val = self.dropdown.currentText()
        if val == "Custom":
            custom_field = self.spinbox.cleanText()
            if custom_field != "":
                self.last_custom_val = int(custom_field)
            return self.last_custom_val
        else:
            return int(val)

    @Slot(int)
    def setval(self, val:int):
        self.spinbox.setValue(val)
    
    def process_input(self, value):
        if value == "Custom":
            self.spinbox.show()
        else:
            self.spinbox.hide()

class ScanParameters(QVBoxLayout):
    def __init__(self, label:str):
        super().__init__()

        self.addWidget(QLabel(f"{label} Controls:"))
        self.addLayout(self.resolution_settings)
        self.addLayout(self.dwell_time)
    def getval(self) -> int:
        resolution = self.resolution_settings.getval()
        dwell = self.dwell_time.getval() - 1
        return resolution, dwell

class LiveScanControls(ScanParameters):
    def __init__(self):
        self.resolution_settings = SettingBoxWithDefaults("Resolution", 256, 16384, 512, defaults=["256", "512", "1024", "2048", "Custom"])
        self.dwell_time = SettingBoxWithDefaults("Dwell Time", 1, 65536, 1, defaults=["1", "2", "4", "8", "16", "Custom"])
        super().__init__("Live")

        self.start_btn = ToggleButton("Start Live Scan", "Stop Live Scan")
        self.addWidget(self.start_btn)

        self.roi_btn = QPushButton("ROI Scan")
        self.roi_btn.setCheckable(True)
        self.addWidget(self.roi_btn)
    def setEnabled(self, enabled=True):
        self.start_btn.setEnabled(enabled)
        self.roi_btn.setEnabled(enabled)







class PhotoScanControls(ScanParameters):
    def __init__(self):
        self.resolution_settings = SettingBoxWithDefaults("Resolution", 256, 16384, 4096, defaults=["512", "1024", "2048", "4096", "8192", "16384", "Custom"])
        self.dwell_time = SettingBoxWithDefaults("Dwell Time", 1, 65536, 8, defaults=["1", "2", "4", "8", "16", "32", "64", "Custom"])
        super().__init__("Photo")
        self.acq_btn = ToggleButton("Acquire Photo", "Abort Photo Scan")
        self.file = BrowseDirectory()

        self.addWidget(self.acq_btn)
        self.addLayout(self.file)
    def setEnabled(self, enabled=True):
        self.acq_btn.setEnabled(enabled)

class CombinedScanControls(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout()
        layout.setSpacing(1)

        self.live = LiveScanControls()
        self.photo = PhotoScanControls()
        self.line = QHLine()

        layout.addLayout(self.live)
        layout.addWidget(self.line)
        layout.addLayout(self.photo)
        self.setLayout(layout)
    def setEnabled(self, enabled=True):
        self.live.setEnabled(enabled)
        self.photo.setEnabled(enabled)


if __name__ == "__main__":
    import sys
    app = QApplication(sys.argv)
    w = QWidget()
    s = PatternImport()
    w.setLayout(s)
    w.show()
    app.exec()