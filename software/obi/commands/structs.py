import struct
import enum
import array

from collections import UserDict
from dataclasses import dataclass

from amaranth import *
from amaranth import ShapeCastable, Shape
from amaranth.lib import enum, data, wiring
from amaranth.lib.wiring import In, Out, flipped

import logging
logger = logging.getLogger()

BIG_ENDIAN = (struct.pack('@H', 0x1234) == struct.pack('>H', 0x1234))

CMD_SHAPE = 4
class CmdType(enum.IntEnum, shape=CMD_SHAPE):
    Synchronize         = 0x0
    Abort               = 0x1
    Flush               = 0x2
    ExternalCtrl        = 0x3
    BeamSelect          = 0x4
    Blank               = 0x5
    Delay               = 0x6

    Array = 0x8

    RasterPixelFill = 0x9

    RasterRegion        = 0xa
    RasterPixel         = 0xb
    RasterPixelRun      = 0xc
    RasterPixelFreeRun  = 0xd
    VectorPixel         = 0xe
    VectorPixelMinDwell = 0xf 

class PayloadLayout(UserDict):
    def unpack_apply(self, leaf_func=eval("lambda key, value: value"), 
                        wrap_func=eval("lambda dict: dict")):
        def unpack(from_dict, to_dict):
            for key, value in from_dict.items():
                if isinstance(value, dict):
                    to_dict[key] = wrap_func(unpack(value, {}))
                else:
                    to_dict[key] = leaf_func(key, value)
            return to_dict
        return unpack(self.data, {})
    @staticmethod
    def convert_shape(value):
        if isinstance(value, data.ShapeCastable):
            value = value.as_shape()._width
        return value
    def flatten(self):
        new_dict = {}
        def transform(key, value):
            new_dict[key] = self.convert_shape(value)
        self.unpack_apply(transform)
        return new_dict
    def field_names(self):
        return list(self.flatten().keys())
    def total_fields(self):
        total = 0
        def add_to_total(key, value):
            nonlocal total
            total += self.convert_shape(value)
        self.unpack_apply(add_to_total)
        return total
    def as_struct_layout(self):
        return self.unpack_apply(
            lambda field, field_width: field_width,
            lambda field_dict: data.StructLayout(field_dict))
    def pack_dict(self, value_dict):
        return self.unpack_apply(lambda key, value : value_dict[key])
    

class BitLayout(PayloadLayout):
    bits_per_field = 1
    def as_struct_layout(self):
        struct_dict = super().as_struct_layout()
        total_bits = self.total_fields()
        assert total_bits <= CMD_SHAPE, f"{total_bits} bits can't fit in {CMD_SHAPE} bits"
        struct_dict["reserved"] = (8-CMD_SHAPE) - total_bits # add padding to header
        return struct_dict
    def pack_fn(self, cmdtype):
        field_values = []
        field_offset = 0
        field_dict = self.flatten()
        for field_name, field_width in field_dict.items():
            field_values.append(f'((value_dict[{field_name!r}] & {(1 << field_width) - 1}) << {field_offset})')
            field_offset += field_width
        field_values.append(f"{str(int(cmdtype))} << {CMD_SHAPE}") # add type field
        funcstr = f'int({" | ".join(field_values)})'
        return funcstr

STRUCT_FORMATS = {
    1: "B",
    2: "H",
}
class ByteLayout(PayloadLayout):
    bits_per_field = 8
    def as_struct_layout(self):
        return self.unpack_apply(
            lambda field, field_width: field_width*self.bits_per_field,
            lambda field_dict: data.StructLayout(field_dict))
    def as_deserialized_states(self):
        deserialized_states = {}
        offset = 8 #first byte at [0:7] is reserved for header
        for field, bytelength in self.flatten().items():
            deserialized_words = {}
            for n in range(bytelength):
                deserialized_words[f"{field}_{n}"] = offset
                offset += 8
            # reverse byte order
            deserialized_states.update(dict(reversed(deserialized_words.items())))
        return deserialized_states
    def pack_fn(self, header_funcstr):
        field_dict = self.flatten()
        structformat = ">B" #first byte = header
        structargs = ""
        for field_name, field_width in field_dict.items():
            structformat += STRUCT_FORMATS.get(field_width)
            structargs += f"value_dict['{field_name}'], "
        func = f'lambda value_dict: struct.pack("{structformat}", {header_funcstr}, {structargs})'
        return eval(func)


##### start commands

class OutputMode(enum.IntEnum, shape = 2):
    SixteenBit          = 0
    EightBit            = 1
    NoOutput            = 2

class BeamType(enum.IntEnum, shape = 2):
    NoBeam              = 0
    Electron            = 1
    Ion                 = 2


class u14(int):
    """
    An integer value in the range(0,16384), representable by a 14 bit register.
    Valid input value for a 14 bit DAC.
    Counters are 0-indexed so 0 = 1 count and 16383 = 16384 counts.
    """
    def __init__(self, val:int):
        if val < 0:
            raise ValueError(f"{val} < 0. Only positive integers are valid")
        if val > 16383:
            raise ValueError(f"{val} > 16383. Value overflows 14 bits")
    def __new__(self, val:int):
        self.__init__(self, val)
        return val & 0b11111111111111

class u16(int):
    """
    An integer value in the range(0,65536), representable by a 16 bit register.
    Counters are 0-indexed so 0 = 1 count and 65535 = 65536 counts.
    """
    def __init__(self, val:int):
        if val < 0:
            raise ValueError(f"{val} < 0. Only positive integers are valid")
        if val > 65535:
            raise ValueError(f"{val} > 16383. Value overflows 14 bits")
    def __new__(self, val:int):
        self.__init__(self, val)
        return val & 0b11111111111111

class fp8_8(int):
    """
    A binary representation of a fractional value with 8 integer bits and 8 fractional bits.
    """
    def __new__(self, val:float):
        return u16(int(val*256))

class DwellTime(u16):
    ''' DwellTime is a descriptive subclass of :class:`u16` used for type annotations. \
        Dwell time is measured in units of ADC cycles.
        
        Important:
            One DwellTime = 125 ns
        '''

@dataclass
class DACCodeRange:
    '''
    A range of DAC codes to be stepped through by internal FPGA counters.\
    DAC step sizes are encoded with fractional bits.

    Args:
        start: The first DAC code to start on.
        count: The number of steps to count up from the starting code.
        step: The step size to increment by each step.
    '''
    start: u14
    count: u14
    step: fp8_8
    def __post_init__(self):
        if self.start > 16383:
            raise ValueError(f"{self.start=} > max position: 16383")
        if self.count > 16384:
            raise ValueError(f"{self.count=} > max resolution: 16384")
        if self.step > 65535:
            raise ValueError("Step size cannot be represented in 16 bits")
    def __repr__(self):
        return f"DACCodeRange(start={self.start}, count={self.count}, step={self.step} - step size {self.step/256:0.03f})"
    @classmethod
    def from_resolution(cls, resolution: u14):
        '''
        Args:
            resolution: Number of pixels to fill entire DAC range
        Returns:
            :class:`DACCodeRange`
        
        As executed, the range of DAC codes will be equivalent to :code:`np.linspace(0,16383,resolution).astype(np.uint16)`

        Example:
            >>> DACCodeRange.from_resolution(2048)
        '''
        return cls(
                start = 0,
                count = resolution,
                step = int((16384/resolution)*256)
            )
    @classmethod
    def from_roi(cls, resolution: u14, start: u14, count: u14):
        '''
        Args:
            resolution: Number of pixels to fill entire DAC range
            start: Starting position for ROI
            count: Length of ROI, in pixels
        Returns:
            :class:`DACCodeRange`
        '''
        return cls(
                start = start*int(16384/resolution),
                count = count,
                step = int((16384/resolution)*256)
            )

@dataclass
class Transforms:
    xflip: bool
    yflip: bool
    rotate90: bool