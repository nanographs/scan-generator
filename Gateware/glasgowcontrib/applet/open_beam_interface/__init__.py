from amaranth import *
from amaranth.lib import enum, data, wiring
from amaranth.lib.fifo import SyncFIFOBuffered
from amaranth.lib.wiring import In, Out, flipped


# Overview of (linear) processing pipeline:
# 1. PC software (in: user input, out: bytes)
# 2. Glasgow software/framework (in: bytes, out: same bytes; vendor-provided)
# 3. Command deserializer (in: bytes; out: structured commands)
# 4. Command parser/executor (in: structured commands, out: DAC state changes and ADC sample strobes)
# 5. DAC (in: DAC words, out: analog voltage; Glasgow plug-in)
# 6. electron microscope
# 7. ADC (in: analog voltage; out: ADC words, Glasgow plug-in)
# 8. Image serializer (in: ADC words, out: image frames)
# 9. Configuration synchronizer (in: image frames, out: image pixels or synchronization frames)
# 10. Frame serializer (in: frames, out: bytes)
# 11. Glasgow software/framework (in: bytes, out: same bytes; vendor-provided)
# 12. PC software (in: bytes, out: displayed image)


def StreamSignature(data_layout):
    return wiring.Signature({
        "data":  Out(data_layout),
        "valid": Out(1),
        "ready": In(1),
        "flush": Out(1)
    })


BusSignature = wiring.Signature({
    "adc_clk":  Out(1),
    "adc_oe":   Out(1),

    "dac_clk":  Out(1),
    "dac_x_le": Out(1),
    "dac_y_le": Out(1),

    "data_i":   In(15),
    "data_o":   Out(15),
    "data_oe":  Out(1),
})

DwellTime = unsigned(16)
class PipelinedLoopbackAdapter(wiring.Component):
    loopback_stream: In(unsigned(14))
    valid: In(1)
    bus: Out(BusSignature)

    def __init__(self, adc_latency: int):
        self.adc_latency = adc_latency
        super().__init__()
    def elaborate(self, platform):
        m = Module()

        prev_bus_adc_oe = Signal()
        adc_oe_falling = Signal()
        m.d.sync += prev_bus_adc_oe.eq(self.bus.adc_oe)
        m.d.comb += adc_oe_falling.eq(prev_bus_adc_oe & ~self.bus.adc_oe)

        shift_register = Signal(14*self.adc_latency)

        with m.If(adc_oe_falling):
            m.d.sync += shift_register.eq((shift_register << 14) | self.loopback_stream)
        
        m.d.comb += self.bus.data_i.eq(shift_register.word_select(self.adc_latency-1, 14))

        return m

class BusController(wiring.Component):
    # FPGA-side interface
    dac_stream: In(StreamSignature(data.StructLayout({
        "dac_x_code": 14,
        "dac_y_code": 14,
        "last":       1,
    })))

    adc_stream: Out(StreamSignature(data.StructLayout({
        "adc_code": 14,
        "adc_ovf":  1,
        "last":     1,
    })))

    # IO-side interface
    bus: Out(BusSignature)

    def __init__(self, *, adc_half_period: int, adc_latency: int):
        assert (adc_half_period * 2) >= 4, "ADC period must be large enough for FSM latency"
        self.adc_half_period = adc_half_period
        self.adc_latency     = adc_latency

        super().__init__()

    def elaborate(self, platform):
        m = Module()

        adc_cycles = Signal(range(self.adc_half_period))
        with m.If(adc_cycles == self.adc_half_period - 1):
            m.d.sync += adc_cycles.eq(0)
            m.d.sync += self.bus.adc_clk.eq(~self.bus.adc_clk)
        with m.Else():
            m.d.sync += adc_cycles.eq(adc_cycles + 1)
        # ADC and DAC share the bus and have to work in tandem. The ADC conversion starts simultaneously
        # with the DAC update, so the entire ADC period is available for DAC-scope-ADC propagation.
        m.d.comb += self.bus.dac_clk.eq(self.bus.adc_clk)

        # Queue; MSB = most recent sample, LSB = least recent sample
        accept_sample = Signal(self.adc_latency)
        # Queue; as above
        last_sample = Signal(self.adc_latency)

        m.submodules.adc_stream_fifo = adc_stream_fifo = \
            SyncFIFOBuffered(depth=self.adc_latency, width=len(self.adc_stream.data.as_value()))
        m.d.comb += [
            self.adc_stream.data.eq(adc_stream_fifo.r_data),
            self.adc_stream.valid.eq(adc_stream_fifo.r_rdy),
            adc_stream_fifo.r_en.eq(self.adc_stream.ready),
        ]

        adc_stream_data = Signal.like(self.adc_stream.data) # FIXME: will not be needed after FIFOs have shapes
        m.d.comb += [
            # Cat(adc_stream_data.adc_code,
            #     adc_stream_data.adc_ovf).eq(self.bus.i),
            adc_stream_data.last.eq(last_sample[self.adc_latency-1]),
            adc_stream_fifo.w_data.eq(adc_stream_data),
        ]

        dac_stream_data = Signal.like(self.dac_stream.data)

        m.d.comb += adc_stream_data.adc_code.eq(self.bus.data_i),

        with m.FSM():
            with m.State("ADC Wait"):
                with m.If(self.bus.adc_clk & (adc_cycles == 0)):
                    m.d.comb += self.bus.adc_oe.eq(1) #give bus time to stabilize before sampling
                    m.next = "ADC Read"

            with m.State("ADC Read"):
                m.d.comb += self.bus.adc_oe.eq(1)
                m.d.comb += adc_stream_fifo.w_en.eq(accept_sample[self.adc_latency-1]) # does nothing if ~adc_stream_fifo.w_rdy
                with m.If(self.dac_stream.valid & adc_stream_fifo.w_rdy):
                    # Latch DAC codes from input stream.
                    m.d.comb += self.dac_stream.ready.eq(1)
                    m.d.sync += dac_stream_data.eq(self.dac_stream.data)
                    # Schedule ADC sample for these DAC codes to be output.
                    m.d.sync += accept_sample.eq(Cat(1,accept_sample))
                    # Carry over the flag for last sample [of averaging window] to the output.
                    m.d.sync += last_sample.eq(Cat(self.dac_stream.data.last,last_sample))
                with m.Else():
                    # Leave DAC codes as they are.
                    # Schedule ADC sample for these DAC codes to be discarded.
                    m.d.sync += accept_sample.eq(Cat(0, accept_sample))
                    # The value of this flag is discarded, so it doesn't matter what it is.
                    m.d.sync += last_sample.eq(Cat(0,last_sample))
                m.next = "X DAC Write"

            with m.State("X DAC Write"):
                m.d.comb += [
                    self.bus.data_o.eq(dac_stream_data.dac_x_code),
                    self.bus.data_oe.eq(1),
                    self.bus.dac_x_le.eq(1),
                ]
                m.next = "Y DAC Write"

            with m.State("Y DAC Write"):
                m.d.comb += [
                    self.bus.data_o.eq(dac_stream_data.dac_y_code),
                    self.bus.data_oe.eq(1),
                    self.bus.dac_y_le.eq(1),
                ]
                m.next = "ADC Wait"


        return m

#=========================================================================
class Supersampler(wiring.Component):
    dac_stream: In(StreamSignature(data.StructLayout({
        "dac_x_code": 14,
        "dac_y_code": 14,
        "dwell_time": 16,
    })))

    adc_stream: Out(StreamSignature(data.StructLayout({
        "adc_code":   14,
    })))

    super_dac_stream: Out(StreamSignature(data.StructLayout({
        "dac_x_code": 14,
        "dac_y_code": 14,
        "last":       1,
    })))

    super_adc_stream: In(StreamSignature(data.StructLayout({
        "adc_code":   14,
        "adc_ovf":    1,  # ignored
        "last":       1,
    })))

    def __init__(self):
        super().__init__()
        self.dac_stream_data = Signal.like(self.dac_stream.data)
        

    def elaborate(self, platform):
        m = Module()

        
        m.d.comb += [
            self.super_dac_stream.data.dac_x_code.eq(self.dac_stream_data.dac_x_code),
            self.super_dac_stream.data.dac_y_code.eq(self.dac_stream_data.dac_y_code),
        ]

        dwell_counter = Signal.like(self.dac_stream_data.dwell_time)
        with m.FSM():
            with m.State("Wait"):
                m.d.comb += self.dac_stream.ready.eq(1)
                with m.If(self.dac_stream.valid):
                    m.d.sync += self.dac_stream_data.eq(self.dac_stream.data)
                    m.d.sync += dwell_counter.eq(0)
                    m.next = "Generate"

            with m.State("Generate"):
                m.d.comb += self.super_dac_stream.valid.eq(1)
                with m.If(self.super_dac_stream.ready):
                    with m.If(dwell_counter == self.dac_stream_data.dwell_time):
                        m.d.comb += self.super_dac_stream.data.last.eq(1)
                        m.next = "Wait"
                    with m.Else():
                        m.d.sync += dwell_counter.eq(dwell_counter + 1)

        running_average = Signal.like(self.super_adc_stream.data.adc_code)
        m.d.comb += self.adc_stream.data.adc_code.eq(running_average)
        with m.FSM():
            with m.State("Start"):
                m.d.comb += self.super_adc_stream.ready.eq(1)
                with m.If(self.super_adc_stream.valid):
                    m.d.sync += running_average.eq(self.super_adc_stream.data.adc_code)
                    with m.If(self.super_adc_stream.data.last):
                        m.next = "Wait"
                    with m.Else():
                        m.next = "Average"

            with m.State("Average"):
                m.d.comb += self.super_adc_stream.ready.eq(1)
                with m.If(self.super_adc_stream.valid):
                    m.d.sync += running_average.eq((running_average + self.super_adc_stream.data.adc_code) >> 1)
                    with m.If(self.super_adc_stream.data.last):
                        m.next = "Wait"
                    with m.Else():
                        m.next = "Average"

            with m.State("Wait"):
                m.d.comb += self.adc_stream.valid.eq(1)
                with m.If(self.adc_stream.ready):
                    m.next = "Start"

        return m

#=========================================================================
class RasterRegion(data.Struct):
    x_start: 14 # UQ(14,0)
    x_count: 14 # UQ(14,0)
    x_step:  16 # UQ(8,8)
    y_start: 14 # UQ(14,0)
    y_count: 14 # UQ(14,0)
    y_step:  16 # UQ(8,8)





class RasterScanner(wiring.Component):
    FRAC_BITS = 8

    roi_stream: In(StreamSignature(RasterRegion))

    dwell_stream: In(StreamSignature(DwellTime))

    abort: In(1)
    #: Interrupt the scan in progress and fetch the next ROI from `roi_stream`.

    dac_stream: Out(StreamSignature(data.StructLayout({
        "dac_x_code": 14,
        "dac_y_code": 14,
        "dwell_time": DwellTime,
    })))

    def elaborate(self, platform):
        m = Module()

        region  = Signal.like(self.roi_stream.data)

        x_accum = Signal(14 + self.FRAC_BITS)
        x_count = Signal.like(region.x_count)
        y_accum = Signal(14 + self.FRAC_BITS)
        y_count = Signal.like(region.y_count)
        m.d.comb += [
            self.dac_stream.data.dac_x_code.eq(x_accum[self.FRAC_BITS:]),
            self.dac_stream.data.dac_y_code.eq(y_accum[self.FRAC_BITS:]),
            self.dac_stream.data.dwell_time.eq(self.dwell_stream.data),
        ]

        with m.FSM():
            with m.State("Get ROI"):
                m.d.comb += self.roi_stream.ready.eq(1)
                with m.If(self.roi_stream.valid):
                    m.d.sync += [
                        region.eq(self.roi_stream.data),
                        x_accum.eq(Cat(C(0, self.FRAC_BITS), self.roi_stream.data.x_start)),
                        x_count.eq(0),
                        y_accum.eq(Cat(C(0, self.FRAC_BITS), self.roi_stream.data.y_start)),
                        y_count.eq(0),
                    ]
                    m.next = "Scan"

            with m.State("Scan"):
                m.d.comb += self.dwell_stream.ready.eq(self.dac_stream.ready)
                m.d.comb += self.dac_stream.valid.eq(self.dwell_stream.valid)
                with m.If(self.dwell_stream.valid & self.dac_stream.ready):
                    # AXI4-Stream §2.2.1
                    # > Once TVALID is asserted it must remain asserted until the handshake occurs.
                    with m.If(self.abort):
                        m.next = "Get ROI"

                    with m.If(x_count == region.x_count):
                        with m.If(y_count == region.y_count):
                            m.next = "Get ROI"
                        with m.Else():
                            m.d.sync += y_accum.eq(y_accum + region.x_step) #use same step for x and y. pixels should be square
                            m.d.sync += y_count.eq(y_count + 1)

                        m.d.sync += x_accum.eq(Cat(C(0, self.FRAC_BITS), self.roi_stream.data.x_start))
                        m.d.sync += x_count.eq(0)
                    with m.Else():
                        m.d.sync += x_accum.eq(x_accum + region.x_step)
                        m.d.sync += x_count.eq(x_count + 1)

        return m

#=========================================================================


Cookie = unsigned(16)
#: Arbitrary value for synchronization. When received, returned as-is in an USB IN frame.


class Command(data.Struct):
    class Type(enum.Enum, shape=8):
        Synchronize     = 0
        RasterRegion    = 1
        RasterPixel     = 2
        RasterPixelRun  = 3
        VectorPixel     = 4
        Control = 5

    type: Type

    class ControlInstruction(enum.Enum, shape = 8):
        Abort = 1
        Flush = 2

    payload: data.UnionLayout({
        "synchronize":      data.StructLayout({
            "cookie":           Cookie,
            "raster_mode":      1,
        }),
        "raster_region":    RasterRegion,
        "raster_pixel":     DwellTime,
        "raster_pixel_run": data.StructLayout({
            "length":           16,
            "dwell_time":       DwellTime,
        }),
        "vector_pixel":     data.StructLayout({
            "x_coord":          14,
            "y_coord":          14,
            "dwell_time":       DwellTime,
        }),
        "control_instruction": ControlInstruction
    })


class CommandParser(wiring.Component):
    usb_stream: In(StreamSignature(8))
    cmd_stream: Out(StreamSignature(Command))


    def elaborate(self, platform):
        m = Module()

        command = Signal(Command)
        m.d.comb += self.cmd_stream.data.eq(command)

        with m.FSM():
            with m.State("Type"):
                m.d.comb += self.usb_stream.ready.eq(1)
                m.d.sync += command.type.eq(self.usb_stream.data)
                with m.If(self.usb_stream.valid):
                    with m.Switch(self.usb_stream.data):
                        with m.Case(Command.Type.Synchronize):
                            m.next = "Payload Synchronize 1 High"

                        with m.Case(Command.Type.RasterRegion):
                            m.next = "Payload Raster Region 1 High"

                        with m.Case(Command.Type.RasterPixel):
                            #m.next = "Payload Raster Pixel Count High"
                            m.next = "Payload Raster Pixel Array High"

                        with m.Case(Command.Type.RasterPixelRun):
                            m.next = "Payload Raster Pixel Run 1 High"

                        with m.Case(Command.Type.VectorPixel):
                            m.next = "Payload Vector Pixel 1 High"
                        
                        with m.Case(Command.Type.Control):
                            m.next = "Payload Control"

            def Deserialize(target, state, next_state):
                #print(f'state: {state} -> next state: {next_state}')
                with m.State(state):
                    m.d.comb += self.usb_stream.ready.eq(1)
                    with m.If(self.usb_stream.valid):
                        m.d.sync += target.eq(self.usb_stream.data)
                        m.next = next_state

            def DeserializeWord(target, state_prefix, next_state):
                if not "Submit" in next_state:
                    next_state += " High"
                #print(f'\tdeserializing: {state_prefix} to {next_state}')
                Deserialize(target[8:16],
                    f"{state_prefix} High", f"{state_prefix} Low")
                Deserialize(target[0:8],
                    f"{state_prefix} Low",  next_state)

            DeserializeWord(command.payload.synchronize.cookie,
                "Payload Synchronize 1", "Payload Synchronize 2")
            # DeserializeWord(command.payload.synchronize.raster_mode,
            #     "Payload Synchronize 2", "Submit")
            Deserialize(command.payload.synchronize.raster_mode, 
                "Payload Synchronize 2 High", "Submit")

            DeserializeWord(command.payload.raster_region.x_start,
                "Payload Raster Region 1", "Payload Raster Region 2")
            DeserializeWord(command.payload.raster_region.x_count,
                "Payload Raster Region 2", "Payload Raster Region 3")
            DeserializeWord(command.payload.raster_region.x_step,
                "Payload Raster Region 3", "Payload Raster Region 4")
            DeserializeWord(command.payload.raster_region.y_start,
                "Payload Raster Region 4", "Payload Raster Region 5")
            DeserializeWord(command.payload.raster_region.y_count,
                "Payload Raster Region 5", "Payload Raster Region 6")
            DeserializeWord(command.payload.raster_region.y_step,
                "Payload Raster Region 6", "Submit")

            raster_pixel_count = Signal(16)
            DeserializeWord(raster_pixel_count,
                "Payload Raster Pixel Count", "Payload Raster Pixel Array")

            DeserializeWord(command.payload.raster_pixel,
                "Payload Raster Pixel Array", "Payload Raster Pixel Array Submit")

            with m.State("Payload Raster Pixel Array Submit"):
                m.d.comb += self.cmd_stream.valid.eq(1)
                with m.If(self.cmd_stream.ready):
                    with m.If(raster_pixel_count == 0):
                        m.next = "Type"
                    with m.Else():
                        m.d.sync += raster_pixel_count.eq(raster_pixel_count - 1)
                        m.next = "Payload Raster Pixel Array Low"

            DeserializeWord(command.payload.raster_pixel_run.length,
                "Payload Raster Pixel Run 1", "Payload Raster Pixel Run 2")
            DeserializeWord(command.payload.raster_pixel_run.dwell_time,
                "Payload Raster Pixel Run 2", "Submit")

            DeserializeWord(command.payload.vector_pixel.x_coord,
                "Payload Vector Pixel 1", "Payload Vector Pixel 2")
            DeserializeWord(command.payload.vector_pixel.y_coord,
                "Payload Vector Pixel 2", "Payload Vector Pixel 3")
            DeserializeWord(command.payload.vector_pixel.dwell_time,
                "Payload Vector Pixel 3", "Submit")

            Deserialize(command.payload.control_instruction, 
                "Payload Control", "Submit")

            with m.State("Submit"):
                m.d.comb += self.cmd_stream.valid.eq(1)
                with m.If(self.cmd_stream.ready):
                    m.next = "Type"

        return m

#=========================================================================
class CommandExecutor(wiring.Component):
    cmd_stream: In(StreamSignature(Command))
    img_stream: Out(StreamSignature(unsigned(16)))

    bus: Out(BusSignature)

    def __init__(self, loopback):
        self.loopback = loopback
        super().__init__()

    def elaborate(self, platform):
        m = Module()

        m.submodules.bus_controller = bus_controller = BusController(adc_half_period=3, adc_latency=6)
        m.submodules.supersampler   = supersampler   = Supersampler()
        m.submodules.raster_scanner = raster_scanner = RasterScanner()

        wiring.connect(m, flipped(self.bus), bus_controller.bus)
        if self.loopback:
            m.submodules.loopback_adapter = loopback_adapter = PipelinedLoopbackAdapter(adc_latency=6)
            wiring.connect(m, self.bus, flipped(loopback_adapter.bus))
            m.d.comb += loopback_adapter.valid.eq(supersampler.super_dac_stream.valid)

        wiring.connect(m, supersampler.super_dac_stream, bus_controller.dac_stream)
        wiring.connect(m, bus_controller.adc_stream, supersampler.super_adc_stream)
        
        vector_stream = StreamSignature(data.StructLayout({
            "dac_x_code": 14,
            "dac_y_code": 14,
            "dwell_time": DwellTime,
        })).create()

        raster_mode = Signal()
        command = Signal.like(self.cmd_stream.data)
        with m.If(raster_mode):
            wiring.connect(m, raster_scanner.dac_stream, supersampler.dac_stream)
            if self.loopback:
                with m.If(command.type == Command.Type.RasterPixel):
                    m.d.comb += loopback_adapter.loopback_stream.eq(supersampler.dac_stream_data.dwell_time)
                with m.Else():
                    m.d.comb += loopback_adapter.loopback_stream.eq(supersampler.super_dac_stream.data.dac_x_code)
        with m.Else():
            wiring.connect(m, vector_stream, supersampler.dac_stream)
            if self.loopback:
                m.d.comb += loopback_adapter.loopback_stream.eq(supersampler.dac_stream_data.dwell_time)

        in_flight_pixels = Signal(4) # should never overflow
        submit_pixel = Signal()
        retire_pixel = Signal()
        m.d.sync += in_flight_pixels.eq(in_flight_pixels + submit_pixel - retire_pixel)

        
        run_length = Signal.like(command.payload.raster_pixel_run.length)
        m.d.comb += [
            raster_scanner.roi_stream.data.eq(command.payload.raster_region),
            vector_stream.data.eq(command.payload.vector_pixel)
            # vector_stream.dac_x_code.eq(command.payload.vector_pixel.x_coord),
            # vector_stream.dac_x_code.eq(command.payload.vector_pixel.y_coord),
            # vector_stream.dwell_time.eq(command.payload.vector_pixel.dwell_time),
        ]

        sync_req = Signal()
        sync_ack = Signal()

        with m.FSM():
            with m.State("Fetch"):
                m.d.comb += self.cmd_stream.ready.eq(1)
                with m.If(self.cmd_stream.valid):
                    m.d.sync += command.eq(self.cmd_stream.data)
                    m.next = "Execute"

            with m.State("Execute"):
                with m.Switch(command.type):
                    with m.Case(Command.Type.Synchronize):
                        m.d.comb += sync_req.eq(1)
                        with m.If(sync_ack):
                            m.d.sync += raster_mode.eq(command.payload.synchronize.raster_mode)
                            m.next = "Fetch"

                    with m.Case(Command.Type.RasterRegion):
                        m.d.comb += raster_scanner.roi_stream.valid.eq(1)
                        with m.If(raster_scanner.roi_stream.ready):
                            m.next = "Fetch"

                    with m.Case(Command.Type.RasterPixel):
                        m.d.comb += [
                            raster_scanner.dwell_stream.valid.eq(1),
                            raster_scanner.dwell_stream.data.eq(command.payload.raster_pixel),
                        ]
                        with m.If(raster_scanner.dwell_stream.ready):
                            m.d.comb += submit_pixel.eq(1)
                            m.next = "Fetch"

                    with m.Case(Command.Type.RasterPixelRun):
                        m.d.comb += [
                            raster_scanner.dwell_stream.valid.eq(1),
                            raster_scanner.dwell_stream.data.eq(command.payload.raster_pixel_run.dwell_time)
                        ]
                        with m.If(raster_scanner.dwell_stream.ready):
                            m.d.comb += submit_pixel.eq(1)
                            with m.If(run_length == command.payload.raster_pixel_run.length):
                                m.d.sync += run_length.eq(0)
                                m.next = "Fetch"
                            with m.Else():
                                m.d.sync += run_length.eq(run_length + 1)

                    with m.Case(Command.Type.VectorPixel):
                        m.d.comb += vector_stream.valid.eq(1)
                        with m.If(vector_stream.ready):
                            m.d.comb += submit_pixel.eq(1)
                            m.next = "Fetch"
                    
                    with m.Case(Command.Type.Control):
                        with m.If(command.payload.control_instruction == Command.ControlInstruction.Abort):
                            m.d.comb += raster_scanner.abort.eq(1)
                        with m.If(command.payload.control_instruction == Command.ControlInstruction.Flush):
                            with m.If(raster_mode):
                                m.d.comb += raster_scanner.roi_stream.flush.eq(1)
                            with m.Else():
                                m.d.comb += vector_stream.flush.eq(1)

                        m.next = "Fetch"

        with m.FSM():
            with m.State("Imaging"):
                m.d.comb += [
                    self.img_stream.data.eq(supersampler.adc_stream.data.adc_code),
                    self.img_stream.valid.eq(supersampler.adc_stream.valid),
                    supersampler.adc_stream.ready.eq(self.img_stream.ready),
                    retire_pixel.eq(supersampler.adc_stream.valid & self.img_stream.ready),
                ]
                with m.If((in_flight_pixels == 0) & sync_req):
                    m.next = "Write FFFF"

            with m.State("Write FFFF"):
                m.d.comb += [
                    self.img_stream.data.eq(0xffff),
                    self.img_stream.valid.eq(1),
                ]
                with m.If(self.img_stream.ready):
                    m.next = "Write cookie"

            with m.State("Write cookie"):
                m.d.comb += [
                    self.img_stream.data.eq(command.payload.synchronize.cookie),
                    self.img_stream.valid.eq(1),
                ]
                with m.If(self.img_stream.ready):
                    m.d.comb += sync_ack.eq(1)
                    m.next = "Imaging"

        return m

#=========================================================================
class ImageSerializer(wiring.Component):
    img_stream: In(StreamSignature(unsigned(16)))
    usb_stream: Out(StreamSignature(8))

    def elaborate(self, platform):
        m = Module()

        high = Signal(8)
        
        with m.FSM():
            with m.State("High"):
                m.d.comb += self.usb_stream.data.eq(self.img_stream.data[8:16])
                m.d.comb += self.usb_stream.valid.eq(self.img_stream.valid)
                m.d.comb += self.img_stream.ready.eq(self.usb_stream.ready)
                m.d.sync += high.eq(self.img_stream.data[0:8])
                with m.If(self.usb_stream.ready & self.img_stream.valid):
                    m.next = "Low"

            with m.State("Low"):
                m.d.comb += self.usb_stream.data.eq(high)
                m.d.comb += self.usb_stream.valid.eq(1)
                with m.If(self.usb_stream.ready):
                    m.next = "High"

        return m

#=========================================================================

from amaranth.build import *
from glasgow.gateware.pads import Pads

obi_resources  = [
    Resource("control", 0,
        Subsignal("power_good", Pins("K1", dir="o")), # D17
        #Subsignal("D18", Pins("J1", dir="o")), # D18
        Subsignal("x_latch", Pins("H3", dir="o")), # D19
        Subsignal("y_latch", Pins("H1", dir="o")), # D20
        Subsignal("a_enable", Pins("G3", dir="o")), # D21
        Subsignal("a_latch", Pins("H2", dir="o")), # D22
        Subsignal("d_clock", Pins("F3", dir="o")), # D23
        Subsignal("a_clock", Pins("G1", dir="o")), # D24
        Attrs(IO_STANDARD="SB_LVCMOS33")
    ),

    Resource("data", 0,
        Subsignal("D1", Pins("B2", dir="io")),
        Subsignal("D2", Pins("B1", dir="io")),
        Subsignal("D3", Pins("C4", dir="io")),
        Subsignal("D4", Pins("C3", dir="io")),
        Subsignal("D5", Pins("C2", dir="io")),
        Subsignal("D6", Pins("C1", dir="io")),
        Subsignal("D7", Pins("D1", dir="io")),
        Subsignal("D8", Pins("D3", dir="io")),
        Subsignal("D9", Pins("F4", dir="io")),
        Subsignal("D10", Pins("G2", dir="io")),
        Subsignal("D11", Pins("E3", dir="io")),
        Subsignal("D12", Pins("F1", dir="io")),
        Subsignal("D13", Pins("E2", dir="io")),
        Subsignal("D14", Pins("F2", dir="io")),
        # Subsignal("D15", Pins("E1", dir="io")),
        # Subsignal("D16", Pins("D2", dir="io")),
        Attrs(IO_STANDARD="SB_LVCMOS33")
    ),
]

class OBISubtarget(wiring.Component):
    def __init__(self, *, out_fifo, in_fifo, sim, loopback):
        self.out_fifo = out_fifo
        self.in_fifo  = in_fifo
        self.sim = sim
        self.loopback = loopback

    def elaborate(self, platform):
        m = Module()

        m.submodules.parser     = parser     = CommandParser()
        m.submodules.executor   = executor   = CommandExecutor(loopback = self.loopback)
        m.submodules.serializer = serializer = ImageSerializer()

        wiring.connect(m, parser.cmd_stream, executor.cmd_stream)
        wiring.connect(m, executor.img_stream, serializer.img_stream)

        if self.sim:
            m.submodules.out_fifo = self.out_fifo
            m.submodules.in_fifo = self.in_fifo

        m.d.comb += [
            parser.usb_stream.data.eq(self.out_fifo.r_data),
            parser.usb_stream.valid.eq(self.out_fifo.r_rdy),
            self.out_fifo.r_en.eq(parser.usb_stream.ready),
            self.in_fifo.w_data.eq(serializer.usb_stream.data),
            self.in_fifo.w_en.eq(serializer.usb_stream.valid),
            self.in_fifo.flush.eq(serializer.usb_stream.flush),
            serializer.usb_stream.ready.eq(self.in_fifo.w_rdy),
        ]

        if not self.sim:
            control = platform.request("control")

            m.d.comb += [
                # platform.request("blah").eq(executor.bus.data_o) ...
                control.x_latch.eq(executor.bus.dac_x_le),
                control.y_latch.eq(executor.bus.dac_y_le),
                control.a_enable.eq(executor.bus.adc_oe),
                control.d_clock.eq(executor.bus.dac_clk),
                control.a_clock.eq(executor.bus.adc_clk),
            ]

            data_lines = platform.request("data")
            
            data = [
                    data_lines.D1,
                    data_lines.D2,
                    data_lines.D3,
                    data_lines.D4,
                    data_lines.D5,
                    data_lines.D6,
                    data_lines.D7,
                    data_lines.D8,
                    data_lines.D9,
                    data_lines.D10,
                    data_lines.D11,
                    data_lines.D12,
                    data_lines.D13,
                    data_lines.D14
                ]

            for i, pad in enumerate(data):
                m.d.comb += [
                    executor.bus.data_i[i].eq(pad.i),
                    pad.o.eq(executor.bus.data_o),
                    pad.oe.eq(executor.bus.data_oe)
                ]

        return m

#=========================================================================


import logging
import random
from glasgow.applet import *

import struct

def ffp_8_8(num: float, print_debug = True): #couldn't find builtin python function for this if there is one
    if print_debug:
        print(f'step: {num}')
    b_str = ""
    assert (num <= pow(2,7))
    for n in range(7, 0, -1):
        b = num//pow(2,n)
        b_str += str(int(b))
        num -= b*pow(2,n)
        if print_debug:
            print(f'2^{n}\t{b}')
    for n in range(0,9):
        b = num//pow(2,-1*n)
        b_str += str(int(b))
        num -= b*pow(2,-1*n)
        if print_debug:
            print(f'2^{-1*n}\t{b}')
    if print_debug:
        print(f'ffp: {b_str}, int: {int(b_str,2)}')
    return int(b_str, 2)
class OBICommands:
    def sync_cookie_raster():
        cmd_sync = Command.Type.Synchronize.value
        cookie = random.randint(1,65535)
        return struct.pack('>bHb', cmd_sync, cookie, 1) 
    def sync_cookie_vector():
        cmd_sync = Command.Type.Synchronize.value
        cookie = random.randint(1,65535)
        return struct.pack('>bHb', cmd_sync, cookie, 0) 
    def raster_region(x_start: int, x_count:int , x_step: float, 
                    y_start: int, y_count: int, y_step: float = None):
        x_step = ffp_8_8(x_step)
        if y_step == None:
            y_step = x_step
        else:
            y_step = ffp_8_8(y_step)
        assert (x_count <= 16384)
        assert (y_count <= 16384)
        assert (x_start <= x_count)
        assert (y_start <= y_count)

        cmd_type = Command.Type.RasterRegion.value

        return struct.pack('>bHHHHH', cmd_type, x_start, x_count, x_step, y_start, y_count)

    def raster_pixel(dwell_time: int):
        assert (dwell_time <= 65535)
        cmd_type = Command.Type.RasterPixel.value
        return struct.pack('>bH', cmd_type, dwell_time)
    
    def raster_pixel_run(length: int, dwell_time: int):
        assert (length <= 65535)
        assert (dwell_time <= 65535)
        cmd_type = Command.Type.RasterPixelRun.value
        return struct.pack('>bHH', cmd_type, length, dwell_time)
    
    def vector_pixel(x_coord: int, y_coord:int, dwell_time: int):
        assert (x_coord <= 16384)
        assert (y_coord <= 16384)
        assert (dwell_time <= 65535)
        cmd_type = Command.Type.VectorPixel.value
        return struct.pack('>bHHH', cmd_type, x_coord, y_coord, dwell_time)
    
    def control(instruction):
        assert (1 <= instruction <= 2)
        cmd_type = Command.Type.Control.value
        return struct.pack('>bb', cmd_type, instruction)

    def abort():
        return OBICommands.control(1)
    
    def flush():
        return OBICommands.control(2)


class OBIInterface:
    def __init__(self, interface, logger, device):
        self.lower   = interface
        self._logger = logger
        self._level  = logging.DEBUG if self._logger.name == __name__ else logging.TRACE
        self._device = device
        self.text_file = open("results.txt", "w+")
    async def stream_vector(self, pattern_gen):
        self.text_file.write("\n WRITTEN: \n")
        read_bytes_expected = 0
        sync_cmd = OBICommands.sync_cookie_vector()
        await self.lower.write(sync_cmd)
        self.text_file.write(str(list(sync_cmd)))
        read_bytes_expected += 4

        while True:
            try: 
                if read_bytes_expected < 512:
                    data = await self.lower.read(read_bytes_expected)
                    self.text_file.write("\n READ: \n")
                    self.text_file.write(str(list(data)))
                    read_bytes_expected = 0

                else:
                    x,y,d = next(pattern_gen)
                    cmd = OBICommands.vector_pixel(x, y, d)
                    await self.lower.write(cmd)
                    self.text_file.write(str(list(cmd)))
                    read_bytes_expected += 2
            except StopIteration:
                print("pattern complete")
                break


from amaranth.sim import Simulator

def duplicate(gen_fn, *args):
    return gen_fn(*args), gen_fn(*args)
class SimulationOBIInterface():
    def __init__(self, dut, lower):
        self.dut = dut
        self.lower = lower
        self.text_file = open("results.txt", "w+")

        self.bench_queue = []
        self.expected_stream = bytearray()

    def queue_sim(self, bench):
        self.bench_queue.append(bench)
    def run_sim(self):
        print("run sim")
        sim = Simulator(self.dut)

        def bench():
            for bench in self.bench_queue:
                while len(self.expected_stream) < 512:
                    try:
                        yield from bench
                    except RuntimeError: #raised StopIteration
                        break
                    finally:
                        yield from self.compare_against_expected()
            print("All done.")

        sim.add_clock(1e-6) # 1 MHz
        sim.add_sync_process(bench)
        with sim.write_vcd("applet_sim.vcd"):
            sim.run()
    
    def compare_against_expected(self):
        read_len = min(512, len(self.expected_stream))
        if read_len < 512:
            yield from self.lower.write(OBICommands.flush())
        data = yield from self.lower.read(read_len)
        self.text_file.write("\n READ: \n")
        self.text_file.write(str(list(data)))

        for n in range(read_len):
            #print(f'expected: {self.expected_stream[n]}, actual: {data[n]}')
            print(f'expected: {hex(self.expected_stream[n])}, actual: {hex(data[n])}')
            assert(data[n] == self.expected_stream[n])
        self.expected_stream = self.expected_stream[read_len:]
    
    def sim_vector_stream(self, stream_gen, *args):

        #read_gen, write_gen = duplicate(stream_gen, *args) 
        write_gen = stream_gen(*args)
    
        bytes_written = 0
        read_bytes_expected = 0

        sync_cmd = OBICommands.sync_cookie_vector()
        yield from self.lower.write(sync_cmd)
        self.text_file.write("\n WRITTEN: \n")
        self.text_file.write(str(list(sync_cmd)))
        self.expected_stream.extend([255,255])
        self.expected_stream.extend(sync_cmd[1:3])
        self.text_file.write("---->\n")

        while True:    
            try:
                if len(self.expected_stream) >= 512:
                    yield from self.compare_against_expected()
                else:
                    x, y, d = next(write_gen)
                    cmd = OBICommands.vector_pixel(x, y, d)
                    yield from self.lower.write(cmd)
                    self.expected_stream.extend(struct.pack('>H',d))
                    self.text_file.write(str(list(cmd)))
                    self.text_file.write("\n")
            except StopIteration:
                print("pattern complete")
                break

        raise StopIteration
    
    def sim_raster_region(self, x_start, x_count,
                            y_start, y_count, dwell_time, run_length):
        sync_cmd = OBICommands.sync_cookie_raster()
        yield from self.lower.write(sync_cmd)
        self.text_file.write("\n WRITTEN: \n")
        self.text_file.write(str(list(sync_cmd)))
        self.expected_stream.extend([255,255])
        self.expected_stream.extend(sync_cmd[1:3])

        x_step = 16384/max((x_count - x_start + 1),(y_count-y_start + 1))
        region_cmd = OBICommands.raster_region(x_start, x_count, x_step,
                            y_start, y_count)
        yield from self.lower.write(region_cmd)
        self.text_file.write(str(list(region_cmd)))

        dwell_cmd = OBICommands.raster_pixel_run(run_length, dwell_time)
        yield from self.lower.write(dwell_cmd)
        self.text_file.write(str(list(dwell_cmd)))
        self.text_file.write("---->\n")


        for y in range(y_count):
            for x in range(x_count):
                x_position = struct.pack('>H', int(x_start + x*x_step))
                self.expected_stream.extend(x_position)
                print(f'x position: {x_position}, expected len: {len(self.expected_stream)}')
                yield
                run_length -= 1
                if run_length == 0:
                    break
                if len(self.expected_stream) > 512:
                    yield from self.compare_against_expected()
            break


        raise StopIteration

    def sim_raster_pattern(self, x_start, x_count,
                        y_start, y_count, stream_gen, *args):

        #read_gen, write_gen = duplicate(stream_gen, *args)
        write_gen = stream_gen(*args)

        sync_cmd = OBICommands.sync_cookie_raster()
        yield from self.lower.write(sync_cmd)
        self.text_file.write("\n WRITTEN: \n")
        self.text_file.write(str(list(sync_cmd)))
        
        self.expected_stream.extend([255,255])
        self.expected_stream.extend(sync_cmd[1:3])

        x_step = 16384/max((x_count - x_start + 1),(y_count-y_start + 1))
        region_cmd = OBICommands.raster_region(x_start, x_count, x_step,
                            y_start, y_count)
        yield from self.lower.write(region_cmd)
        self.text_file.write(str(list(region_cmd)))
        self.text_file.write("---->\n")

        run_length = 0

        while True:    
            try:
                if len(self.expected_stream) >= 512:
                    yield from self.compare_against_expected()
                else:
                    d = next(write_gen)
                    run_length += 1
                    print(f'run length: {run_length}')
                    cmd = OBICommands.raster_pixel(d)
                    yield from self.lower.write(cmd)
                    self.expected_stream.extend(struct.pack('>H',d))
                    self.text_file.write(str(list(cmd)))
                    self.text_file.write("\n")
            except StopIteration:
                print("pattern complete")
                break

        raise StopIteration

from glasgow.support.endpoint import ServerEndpoint
class OBIApplet(GlasgowApplet):
    logger = logging.getLogger(__name__)
    help = "open beam interface"
    description = """
    Scanning beam control applet
    """

    @classmethod
    def add_build_arguments(cls, parser, access):
        super().add_build_arguments(parser, access)

        parser.add_argument("--loopback",
            dest = "loopback", action = 'store_true', 
            help = "connect output and input streams internally")
        parser.add_argument("--sim",
            dest = "sim", action = 'store_true', 
            help = "simulate applet instead of actually building")
        
    
    def build(self, target, args):
        if not args.sim:
            self.mux_interface = iface = \
                target.multiplexer.claim_interface(self, args=None, throttle="none")

            target.platform.add_resources(obi_resources)
            
            subtarget = OBISubtarget(
                in_fifo=iface.get_in_fifo(auto_flush=True),
                out_fifo=iface.get_out_fifo(),
                sim = args.sim,
                loopback = args.loopback
            )
            return iface.add_subtarget(subtarget)

        if args.sim:
            from glasgow.access.simulation import SimulationMultiplexerInterface, SimulationDemultiplexerInterface
            from glasgow.device.hardware import GlasgowHardwareDevice

            self.mux_interface = iface = SimulationMultiplexerInterface(OBIApplet)

            in_fifo = iface._in_fifo = iface.get_in_fifo(auto_flush=False, depth = 512)
            out_fifo = iface._out_fifo = iface.get_out_fifo(depth = 512)

            iface = SimulationDemultiplexerInterface(GlasgowHardwareDevice, OBIApplet, iface)

            dut = OBISubtarget(
                in_fifo = in_fifo, 
                out_fifo = out_fifo, 
                sim = args.sim, 
                loopback = args.loopback)
            
            sim_iface = SimulationOBIInterface(dut, iface)

            
            def vector_rectangle(x_width, y_height):
                for y in range(0, y_height):
                    for x in range(0, x_width):
                        yield [x, y, x+y]

            def raster_rectangle(x_width, y_height):
                for y in range(0, 5):
                    for x in range(0, x_width):
                        yield x+y
                
            # bench1 = sim_iface.sim_vector_stream(vector_rectangle, 10,10)
            # sim_iface.queue_sim(bench1)

            # bench2 = sim_iface.sim_raster_region(255, 511, 0, 255, 2, 200)
            # sim_iface.queue_sim(bench2)

            bench3 = sim_iface.sim_raster_pattern(0, 255, 0, 255, raster_rectangle, 256, 256)
            sim_iface.queue_sim(bench3)
            sim_iface.run_sim()

            

    async def run(self, device, args):
        iface = await device.demultiplexer.claim_interface(self, self.mux_interface, args=None)

        obi_iface = OBIInterface(iface, self.logger, device)

        return obi_iface


    

    @classmethod
    def add_interact_arguments(cls, parser):
        ServerEndpoint.add_argument(parser, "endpoint")

    async def interact(self, device, args, obi_iface):
        while True:
            try:
                data = await asyncio.shield(endpoint.recv())
                await obi_iface.lower.write(data)
            except asyncio.CancelledError:
                pass
        

        

    

