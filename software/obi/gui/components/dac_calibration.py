import sys
import asyncio
import numpy as np

from PyQt6.QtWidgets import (QLabel, QGridLayout, QApplication, QWidget, QProgressBar, QTabWidget,
                             QSpinBox, QComboBox, QHBoxLayout, QVBoxLayout, QPushButton)
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtCore import QSize

import qasync
from qasync import asyncSlot, asyncClose, QApplication, QEventLoop
import pyqtgraph as pg

from obi.transfer import TCPConnection
from obi.commands import VectorPixelCommand, OutputMode, SynchronizeCommand, FlushCommand, DACCodeRange
from obi.macros import RasterScanCommand
from .scan_parameters import ToggleButton


class DACSettings(QHBoxLayout):
    def __init__(self, name):
        super().__init__()
        self.addWidget(QLabel(name))
        self.max_btn = QPushButton("Max")
        self.max_btn.clicked.connect(self.maxClicked)
        self.mid_btn = QPushButton("Mid")
        self.mid_btn.clicked.connect(self.midClicked)
        self.min_btn = QPushButton("Min")
        self.min_btn.clicked.connect(self.minClicked)

        self.field = QSpinBox()
        self.field.setRange(0, 16383)
        self.field.setSingleStep(1)
        self.field.setValue(1)

        self.addWidget(self.max_btn)
        self.addWidget(self.mid_btn)
        self.addWidget(self.min_btn)
        self.addWidget(self.field)

        self.midClicked() #set to mid value when on initialization

    def maxClicked(self):
        self.field.setValue(16383)
    
    def midClicked(self):
        self.field.setValue(8191)
    
    def minClicked(self):
        self.field.setValue(0)

class ADCSettings(QHBoxLayout):
    def __init__(self):
        super().__init__()
        self.addWidget(QLabel("ADC Reading:"))
        self.field = QLabel("")
        self.addWidget(self.field)


class ADCTest(QVBoxLayout):
    def __init__(self, conn):
        self.conn = conn
        super().__init__()

        self.y_btn = QPushButton("Y")
        self.y_btn.setCheckable(True)
        self.scan_btn = QPushButton("scan")
        self.exp_btn = QPushButton("copy to clipboard 📋")
        self.scan_btn.clicked.connect(self.scan)
        self.exp_btn.clicked.connect(self.exp)

        self.data = np.ndarray(16384)

        self.plot = pg.PlotWidget()
        self.plot.setYRange(0,16384)
        self.plot.setXRange(0,16384)

        self.plot_data = pg.PlotDataItem()
        self.plot.addItem(self.plot_data)
        self.plot_data.setData(self.data)
        self.plot_data.setPen(width=2)
        self.plot.setLimits(xMin=0,xMax=16383, yMin=0,yMax=16383)

        self.stop = None
        self.start = None
        self.text = pg.TextItem()
        self.plot.addItem(self.text)
        self.text.setPos(8000, 4000)
        self.text.setFont(QFont('Arial', 18)) 

        self.addWidget(self.y_btn)
        self.addWidget(self.plot)
        self.addWidget(self.scan_btn)
        self.addWidget(self.exp_btn)
    
    @asyncSlot()
    async def scan(self):
        if not self.start == None:
            self.plot.removeItem(self.start)
        if not self.stop == None:
            self.plot.removeItem(self.stop)
        self.text.setText("")
        x = DACCodeRange.from_resolution(16384)
        y = DACCodeRange(start=8192, count=1, step=1)
        print(f"{x, y}")
        if self.y_btn.isChecked():
            cmd = RasterScanCommand(cookie=123,x_range=y, y_range=x, dwell_time=500)
        else:
            cmd = RasterScanCommand(cookie=123,x_range=x, y_range=y, dwell_time=500)
        ptr = 0
        self.scan_btn.setEnabled(False)
        async for chunk in self.conn.transfer_multiple(cmd, latency=16384):
            l = len(chunk)
            self.data[ptr:ptr+l] = chunk
            self.plot_data.setData(self.data)
            ptr += l
        
        x_start = 0
        x_stop = 16383
        for x in range(0, 16383):
            if self.data[x] > 0:
                x_start = x
                break

        for x in range(16383,0,-1):
            if self.data[x] < 16383:
                x_stop = x
                break
        
        try:
            text = ""
            text += f"linear region start: {x_start}\n"
            text += f"linear region stop: {x_stop}\n"

            slope, intercept = np.polyfit(np.array(range(x_start, x_stop)), self.data[x_start:x_stop], 1)
            text += f"slope: {slope:0.05f}\n"
            text += f"y-intercept: {intercept:0.05f}\n"

            correlation = np.corrcoef(np.array(range(x_start, x_stop)), self.data[x_start:x_stop])[0,1]
            text += f"R^2: {correlation}"

            self.start = pg.InfiniteLine(movable=False, angle=90)
            self.stop = pg.InfiniteLine(movable=False, angle=90)
            self.start.setPos([x_start,0])
            self.stop.setPos([x_stop,0])
            self.plot.addItem(self.start)
            self.plot.addItem(self.stop)
            self.text.setText(text)
        except:
            print("Could not fit line")
        self.scan_btn.setEnabled(True)
    
    def exp(self):
        exporter = pg.exporters.ImageExporter(self.plot.plotItem)
        exporter.export("adc.png", copy=True)
    

class DACTest(QVBoxLayout):
    def __init__(self, conn):
        self.conn = conn
        super().__init__()
        self.x_settings = DACSettings("X")
        self.y_settings = DACSettings("Y")
        self.adc_settings = ADCSettings()
        self.start_btn = ToggleButton("Start", "Stop")
        self.start_btn.clicked.connect(self.toggle_live)


        self.pts = 1000
        self.data = np.ndarray(self.pts)
        #self.ptr = 0

        self.plot = pg.PlotWidget(enableMenu=False)
        self.plot.setYRange(0,16384)
        self.plot.setXRange(0,1000)
        
        self.plot_data = pg.PlotDataItem()
        self.plot.addItem(self.plot_data)
        self.plot_data.setData(self.data)
        self.plot.setMouseEnabled(x=False, y=True)
        self.plot.setLimits(xMin=0,xMax=self.pts, yMin=0,yMax=16383)

        mid = pg.InfiniteLine(movable=False, angle=0)
        mid.setPos([0,8191])
        self.plot.addItem(mid)

        self.addWidget(self.plot)
        self.addLayout(self.x_settings)
        self.addLayout(self.y_settings)

        h = QHBoxLayout()
        h.addLayout(self.adc_settings)
        h.addWidget(self.start_btn)
        self.addLayout(h)
    
    def getvals(self):
        x_coord = int(self.x_settings.field.cleanText())
        y_coord = int(self.y_settings.field.cleanText())
        return x_coord, y_coord
    
    @asyncSlot()
    async def setvals(self):
        x_coord, y_coord = self.getvals()
        print(f"{x_coord=}, {y_coord=}")
        cmd = VectorPixelCommand(x_coord = x_coord, y_coord = y_coord, dwell_time=100)
        # carray = bytearray()
        # for _ in range(16384):
        #     carray.extend(bytes(cmd))
        return await self.conn.transfer(cmd)
        #await self.conn.transfer_bytes(carray)

    def display_data(self, data):
        self.adc_settings.field.setText(f"{data[0]}")
        self.data[:self.pts-1] = self.data[1:self.pts]
        self.data[self.pts-1] = data[0]
        self.plot_data.setData(self.data)

    
    @asyncSlot()
    async def toggle_live(self):
        stop = asyncio.Event()
        self.start_btn.to_live_state(stop.set)
        
        while not stop.is_set():
            data = await self.setvals()
            self.display_data(data)
        
        self.start_btn.to_paused_state(self.toggle_live)
        print("done")



class RangeLineCtrl(QHBoxLayout):
    pen_normal = pg.mkPen(color = "#ff4f00", width = 1)
    pen_highlight = pg.mkPen(color = "#ee4b2b", width = 1)
    def __init__(self, name:str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.line = None
        self.set_btn = QPushButton(f"Set {name}")
        self.label = QLabel("")
        self.addWidget(self.set_btn)
        self.addWidget(self.label)
    def setLine(self, value):
        self.label.setText(f"{value}") 
        self.line = pg.InfiniteLine(movable=False, angle=0, pen = self.pen_normal)
        self.line.setPos([0,value])
        return self.line
    def highlightLine(self):
        self.line.setPen(self.pen_highlight)
    def getval(self):
        return int(self.label.text())

class PotTest(DACTest):
    def __init__(self, conn):
        super().__init__(conn)
        images = QHBoxLayout()
        adcimg = QLabel()
        adcimg.setPixmap(QPixmap("/Users/isabelburgos/Open-Beam-Interface/software/docs/source/_static/ADC_adjustment.png").scaled(QSize(350,100)))
        images.addWidget(adcimg)
        dacimg = QLabel()
        dacimg.setPixmap(QPixmap("/Users/isabelburgos/Open-Beam-Interface/software/docs/source/_static/DAC_adjustment.png").scaled(QSize(350,100)))
        images.addWidget(dacimg)
        self.addLayout(images)

        h = QHBoxLayout()
        self.maxrange = RangeLineCtrl("Max")
        self.maxrange.setLine(16383)
        h.addLayout(self.maxrange)
        self.maxrange.set_btn.clicked.connect(self.setMax)
        self.minrange = RangeLineCtrl("Min")
        self.minrange.setLine(0)
        h.addLayout(self.minrange)
        self.minrange.set_btn.clicked.connect(self.setMin)
        self.addLayout(h)

        self.start_range_btn = QPushButton("Start Test")
        self.exp_btn = QPushButton("copy to clipboard 📋")
        self.start_range_btn.clicked.connect(self.doTest)
        self.exp_btn.clicked.connect(self.exp)
        hh = QHBoxLayout()
        hh.addWidget(self.start_range_btn)
        hh.addWidget(self.exp_btn)
        self.addLayout(hh)
        
    def setLine(self, rangectrl:RangeLineCtrl):
        try:
            value = int(self.adc_settings.field.text())
            line = rangectrl.setLine(value)
            self.plot.addItem(line)
        except: 
            print("invalid value")

    def setMax(self):
        self.setLine(self.maxrange)
    def setMin(self):
        self.setLine(self.minrange)
    
    @asyncSlot()
    async def toggle_live(self):
        self.start_range_btn.setEnabled(False)
        await super().toggle_live()
        self.start_range_btn.setEnabled(True)

    @asyncSlot()
    async def doTest(self):
        self.start_btn.setEnabled(False)
        self.start_range_btn.setEnabled(False)
        maxrange = self.maxrange.getval()
        minrange = self.minrange.getval()
        if not maxrange > minrange:
            print(f"Max {maxrange} is not greater than Min {minrange}")
            return
        n = 0
        wentOutOfRange = True
        while n <= 4:
            self.start_range_btn.setText(f"In progress.... {n}")
            data = await self.setvals()
            datapoint = data[0]
            if minrange < datapoint < maxrange:
                wentOutOfRange = False
                self.display_data(data)
            else:
                if not wentOutOfRange:
                    n += 1
                    wentOutOfRange = True

        self.start_range_btn.setEnabled(True)
        self.start_range_btn.setText("Start Test")
        self.start_btn.setEnabled(True)
    
    def exp(self):
        exporter = pg.exporters.ImageExporter(self.plot.plotItem)
        exporter.export("pots.png", copy=True)
    


class XYDACSettings(QVBoxLayout):
    def __init__(self, conn):
        self.conn = conn
        super().__init__()
        self.addWidget(QLabel("✨✨✨welcome to the test and calibration interface✨✨✨"))

        self.tabs = QTabWidget()
        dac_tab = QWidget()
        pot_tab = QWidget()
        adc_tab = QWidget()
        self.tabs.addTab(dac_tab, "DAC")
        self.tabs.addTab(pot_tab, "POT")
        self.tabs.addTab(adc_tab, "ADC")
        dac = DACTest(self.conn)
        adc = ADCTest(self.conn)
        pot = PotTest(self.conn)
        dac_tab.setLayout(dac)
        adc_tab.setLayout(adc)
        pot_tab.setLayout(pot)
        self.addWidget(self.tabs)
    




def run_gui():
    app = QApplication(sys.argv)

    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)

    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)

    w = QWidget()
    conn = TCPConnection('localhost', 2224)
    s = XYDACSettings(conn)
    w.setLayout(s)
    w.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())


if __name__ == "__main__":
    run_gui()