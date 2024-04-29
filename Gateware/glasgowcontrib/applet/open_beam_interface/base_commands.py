from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
import enum

class CommandType(enum.IntEnum):
    Synchronize         = 0x00
    Abort               = 0x01
    Flush               = 0x02
    Delay               = 0x03
    ExternalCtrl        = 0x04
    Blank               = 0x05

    RasterRegion        = 0x10
    RasterPixels        = 0x11
    RasterPixelRun      = 0x12
    RasterPixelFreeRun  = 0x13
    VectorPixel         = 0x14

class OutputMode(enum.IntEnum):
    SixteenBit          = 0
    EightBit            = 1
    NoOutput            = 2

class BeamType(enum.IntEnum):
    Electron = 1
    Ion = 2

@dataclass
class DACCodeRange:
    start: int # UQ(14,0)
    count: int # UQ(14,0)
    step:  int # UQ(8,8)

class DwellTime(int):
    '''Dwell time is measured in units of ADC cycles.
        One DwellTime = 125 ns'''
    pass

class Command(metaclass=ABCMeta):
    def __init_subclass__(cls):
        cls._logger = logger.getChild(f"Command.{cls.__name__}")

    @abstractmethod
    @property
    def message(self):
        ...
    
    @abstractmethod
    @property
    def response(self):
        return []



class SynchronizeCommand(Command):
    def __init__(self, *, cookie: int, raster_mode: bool, output_mode: OutputMode=OutputMode.SixteenBit):
        assert cookie in range(0x0001, 0x10000, 2) # odd cookies only
        self._cookie = cookie
        self._raster_mode = raster_mode
        self._output_mode = output_mode

    def __repr__(self):
        return f"SynchronizeCommand(cookie={self._cookie}, mode={self._mode} [raster_mode={self._raster_mode}, output_mode={self._output_mode}])"

    @property
    def message(self):
        combined = int(self._output_mode<<1 | self._raster_mode)
        return struct.pack(">BHB", CommandType.Synchronize, self._cookie, combined)

class AbortCommand(Command):
    def __repr__(self):
        return f"AbortCommand"
    
    @property
    def message(self):
        return struct.pack(">B", CommandType.Abort)
    

class DelayCommand(Command):
    def __init__(self, delay):
        assert delay <= 65535
        self._delay = delay

    def __repr__(self):
        return f"DelayCommand(delay={self._delay})"

    @property
    def message(self):
        return struct.pack(">BH", CommandType.Delay, self._delay)
    

class BlankCommand(Command):
    def __init__(self, enable:bool, beam_type:BeamType):
        assert (beam_type == BeamType.Electron) | (beam_type == BeamType.Ion)
        self._enable = enable
        self._beam_type = beam_type

    def __repr__(self):
        return f"_BlankCommand(enable={self._enable}, beam_type={self._beam_type})"

    def message(self):
        combined = int(self._beam_type<<1 | self._enable)
        return struct.pack(">BB", CommandType.Blank, combined)

class ExternalCtrlCommand(Command):
    def __init__(self, enable:bool, beam_type:BeamType):
        assert (beam_type == BeamType.Electron) | (beam_type == BeamType.Ion)
        self._enable = enable
        self._beam_type = beam_type

    def __repr__(self):
        return f"_ExternalCtrlCommand(enable={self._enable}, beam_type={self._beam_type})"

    @property
    def message(self):
        combined = int(self._beam_type<<1 | self._enable)
        return struct.pack(">BB", CommandType.ExternalCtrl, combined)

class RasterRegionCommand(Command):
    def __init__(self, *, x_range: DACCodeRange, y_range: DACCodeRange):
        self._x_range = x_range
        self._y_range = y_range

    def __repr__(self):
        return f"_RasterRegionCommand(x_range={self._x_range}, y_range={self._y_range})"

    @property
    def message(self):
        return struct.pack(">BHHHHHH", CommandType.RasterRegion,
            self._x_range.start, self._x_range.count, self._x_range.step,
            self._y_range.start, self._y_range.count, self._y_range.step)

class RasterPixelsCommand(Command):
    def __init__(self, *, dwells: list[DwellTime]):
        assert len(dwells) <= 65536
        self._dwells  = dwells
        
    def __repr__(self):
        return f"_RasterPixelsCommand(dwells=<list of {len(self._dwells)}>)"

    @property
    def message(self):
        commands = bytearray()
        commands.extend(struct.pack(">BH", CommandType.RasterPixels, len(self._dwells) - 1))
        commands.extend(self._dwells)
        return commands

class RasterPixelRunCommand(Command):
    def __init__(self, *, dwell: DwellTime, length: int):
        assert dwell <= 65536
        assert length <= 65536, "Run length counter is 16 bits"
        self._dwell   = dwell
        self._length  = length

    def __repr__(self):
        return f"_RasterPixelRunCommand(dwell={self._dwell}, length={self._length})"

    @property
    def message(self):
        return struct.pack(">BHH", CommandType.RasterPixelRun, self._length - 1, self._dwell)

class _VectorPixelCommand(Command):
    def __init__(self, *, x_coord: int, y_coord: int, dwell: DwellTime):
        assert x_coord <= 65535
        assert y_coord <= 65535
        assert dwell <= 65536
        self._x_coord = x_coord
        self._y_coord = y_coord
        self._dwell   = dwell

    def __repr__(self):
        return f"_VectorPixelCommand(x_coord={self._x_coord}, y_coord={self._y_coord}, dwell={self._dwell})"

    @property
    def message(self):
        return struct.pack(">BHHH", CommandType.VectorPixel, self._x_coord, self._y_coord, self._dwell-1)


            






