import unittest
import struct
import array
from amaranth.sim import Simulator, Tick
from amaranth import Signal, ShapeCastable, Const
from amaranth import DriverConflict
from abc import ABCMeta, abstractmethod
import asyncio

import logging
logger = logging.getLogger()

from obi.applet.open_beam_interface import StreamSignature
from obi.applet.open_beam_interface import Supersampler, RasterScanner, RasterRegion
from obi.applet.open_beam_interface import CommandParser, CommandExecutor, Command, BeamType, OutputMode, CmdType
from obi.applet.open_beam_interface import BusController
from obi.commands import *
from obi.commands.structs import Transforms


## support functions for prettier output
def unpack_const(data):
    newmembers = {}
    members = data.shape().members
    for member in members:
        field = data.__getattr__(member)
        newmembers[member] = field
    return newmembers

def unpack_dict(data):
    all_data = {}
    for member in data:
        try:
            unpacked_data = unpack_const(data.get(member))
            all_data[member] = unpack_dict(unpacked_data)
        except Exception as e:
            all_data[member] = data.get(member)
    return all_data

def prettier_dict(data):
    try:
        data = unpack_const(data)
        if isinstance(data, dict):
            all_data = unpack_dict(data)
        else:
            all_data = data
    except: all_data = data
    return all_data

def filtered_dict(data, payload):
    data = prettier_dict(data)
    filtered_data = {}
    def unpack(data, payload, filtered_data):
        for signal, payload_value in payload.items():
            data_value = data[signal]
            if isinstance(data_value, dict):
                filtered_data[signal] = {}
                unpack(data_value, payload_value, filtered_data[signal])
            else:
                filtered_data[signal] = data_value
    if isinstance(data, dict):
        unpack(data, payload, filtered_data)
    else:
        filtered_data = data
    return filtered_data
    
def prettier_diff(data, payload:dict):
    summary = "\nSignal \t Expected \t Actual"
    data = filtered_dict(data, payload)
    def unpack_diff(data, payload):
        nonlocal summary
        for signal, payload_value in payload.items():
            data_value = data[signal]
            if isinstance(data_value, dict):
                unpack_diff(data_value, payload_value)
            else:
                summary += f"\n{signal}\t {payload_value}\t {data_value}"
                if payload_value != data_value:
                    summary += "\t<---"
    if isinstance(data, dict):
        unpack_diff(data, payload)
    else:
        summary += f"\n \t {payload} \t {data}"
    return summary


# Submit a test payload into a stream. Paayload gets put into stream.
async def put_stream(ctx, stream, payload, timeout_steps=10):
    ctx.set(stream.payload, payload)
    ctx.set(stream.valid, 1)
    ready = False
    timeout = 0
    while not ready:
        ready = ctx.get(stream.ready)
        logger.debug(f"put_stream: {ready=}, {timeout=}/{timeout_steps}")
        await ctx.tick()
        timeout += 1; assert timeout < timeout_steps
    logger.debug(f"put_stream: {ready=}, {timeout=}/{timeout_steps}")
    ctx.set(stream.valid, 0)

# Receive and validate a test payload from a stream. Payload gets compared against stream output.
async def get_stream(ctx, stream, payload, timeout_steps=10):
    ctx.set(stream.ready, 1)
    valid = False
    timeout = 0
    while not valid:
        _, _, valid, data = await ctx.tick().sample(stream.valid, stream.payload)
        logger.debug(f"get_stream: {valid=}, data={filtered_dict(data, payload)}")
        timeout += 1; assert timeout < timeout_steps
    logger.debug(f"get_stream: {valid=}, data={filtered_dict(data, payload)}")
    if isinstance(payload, dict):
        wrapped_payload = stream.payload.shape().const(payload)
    else:
        wrapped_payload = payload
    assert data == wrapped_payload, f"{prettier_diff(data, payload)}"
    ctx.set(stream.ready, 0)

class OBIAppletTestCase(unittest.TestCase):
    '''
    Creates a simulation with a set of testbenches
    
    Attributes
    ----------
    testbench - an Amaranth testbench bench(ctx) see amaranth.SimulatorContext
    name - str - name of vcd file to be generated
    '''
    def simulate(self, dut, testbenches, *, name="test"):
        logger.debug(f"running {name}")
        sim = Simulator(dut)
        sim.add_clock(20.83e-9)
        for testbench in testbenches:
            sim.add_testbench(testbench)
        with sim.write_vcd(f"{name}.vcd"), sim.write_vcd(f"{name}+d.vcd", fs_per_delta=250_000):
            sim.run()

    ## Bus Controller
    def test_bus_controller_streams(self):
        def test_one_cycle():
            dut = BusController(adc_half_period=3, adc_latency=6)
            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 123, "dac_y_code": 456, "last": 1})

            async def get_testbench(ctx):
                await get_stream(ctx, dut.adc_stream, {"adc_code": 0, "last": 1}, timeout_steps=100)

            self.simulate(dut, [put_testbench, get_testbench], name="bus_controller_1")
        
        def test_multi_cycle():
            dut = BusController(adc_half_period=3, adc_latency=6)
            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 1, "dac_y_code": 1, "last": 0})
                await ctx.tick().repeat(1)
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 1, "dac_y_code": 1, "last": 0})
                await ctx.tick().repeat(1)
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 1, "dac_y_code": 1, "last": 0})
                await ctx.tick().repeat(1)
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 1, "dac_y_code": 1, "last": 1})
                await ctx.tick().repeat(1)
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 2, "dac_y_code": 2, "last": 0})
                
            async def get_testbench(ctx):
                await get_stream(ctx, dut.adc_stream, {"adc_code": 0, "last": 0}, timeout_steps=100)
                await get_stream(ctx, dut.adc_stream, {"adc_code": 0, "last": 0}, timeout_steps=100)
                await get_stream(ctx, dut.adc_stream, {"adc_code": 0, "last": 0}, timeout_steps=100)
                await get_stream(ctx, dut.adc_stream, {"adc_code": 0, "last": 1}, timeout_steps=100)

            self.simulate(dut, [put_testbench, get_testbench], name="bus_controller_4")
        
        test_one_cycle()
        test_multi_cycle()
        
    def test_bus_controller_transforms(self):
        def test_xflip(xin: int, xout: int):
            dut = BusController(adc_half_period=3, adc_latency=6, transforms = Transforms(xflip=True, yflip=False, rotate90=False))
            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": xin, "dac_y_code": 0, "last": 0})
                trans_x, trans_y = ctx.get(dut.dac_x_code_transformed), ctx.get(dut.dac_y_code_transformed)
                assert trans_x == xout, f"flipped x {trans_x} != expected {xout}"
                assert trans_y == 0, f"non-flipped y {trans_y} != expected 0"
                
            self.simulate(dut, [put_testbench], name="xflip_{xin}_{xout}")
        
        def test_yflip(yin: int, yout: int):
            dut = BusController(adc_half_period=3, adc_latency=6, transforms = Transforms(xflip=False, yflip=True, rotate90=False))
            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 0, "dac_y_code": yin, "last": 0})
                trans_x, trans_y = ctx.get(dut.dac_x_code_transformed), ctx.get(dut.dac_y_code_transformed)
                assert trans_x == 0, f"non-flipped x {trans_x} != expected 0"
                assert trans_y == yout, f"flipped y {trans_y} != expected {yout}"
                
            self.simulate(dut, [put_testbench], name="xflip_{xin}_{xout}")
        
        def test_rotate90():
            dut = BusController(adc_half_period=3, adc_latency=6, transforms = Transforms(xflip=False, yflip=False, rotate90=True))
            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream, {"dac_x_code": 123, "dac_y_code": 456, "last": 0})
                trans_x, trans_y = ctx.get(dut.dac_x_code_transformed), ctx.get(dut.dac_y_code_transformed)
                assert trans_x == 456, f"rotated x {trans_x} != expected 456"
                assert trans_y == 123, f"rotated y {trans_y} != expected 123"
                
            self.simulate(dut, [put_testbench], name="xflip_{xin}_{xout}")
        
        test_xflip(0, 16383)
        test_xflip(16383, 0)
        test_xflip(1, 16382)
        test_xflip(16382, 1)
        test_yflip(0, 16383)
        test_yflip(16383, 0)
        test_yflip(1, 16382)
        test_yflip(16382, 1)
        test_rotate90()
    
    ## Supersampler
    def test_supersampler_expand(self):
        def run_test(dwell):
            logger.debug(f"dwell {dwell}")
            dut = Supersampler()

            async def put_testbench(ctx):
                await put_stream(ctx, dut.dac_stream,
                    {"dac_x_code": 123, "dac_y_code": 456, "dwell_time": dwell})

            async def get_testbench(ctx):
                for index in range(dwell + 1):
                    await get_stream(ctx, dut.super_dac_stream,
                        {"dac_x_code": 123, "dac_y_code": 456, "last": int(index == dwell)})
                assert ctx.get(dut.super_dac_stream.valid) == 0

            self.simulate(dut, [put_testbench, get_testbench], name="ss_expand")

        run_test(0)
        run_test(1)
        run_test(2)
    
    def test_supersampler_average1(self):
        dut = Supersampler()

        async def put_testbench(ctx):
            await put_stream(ctx, dut.super_adc_stream,
                {"adc_code": 123, "adc_ovf": 0, "last": 1})

        async def get_testbench(ctx):
            await get_stream(ctx, dut.adc_stream,
                {"adc_code": 123})
            assert ctx.get(dut.adc_stream.valid) == 0

        self.simulate(dut, [put_testbench, get_testbench], name = "ss_avg1")

    def test_supersampler_average2(self):
        dut = Supersampler()

        async def put_testbench(ctx):
            await put_stream(ctx, dut.super_adc_stream,
                {"adc_code": 456, "adc_ovf": 0, "last": 0})
            await put_stream(ctx, dut.super_adc_stream,
                {"adc_code": 123, "adc_ovf": 0, "last": 1})
            await put_stream(ctx, dut.super_adc_stream,
                {"adc_code": 999, "adc_ovf": 0, "last": 0})

        async def get_testbench(ctx):
            await get_stream(ctx, dut.adc_stream,
                {"adc_code": (456+123)//2})
            assert ctx.get(dut.adc_stream.valid) == 0

        self.simulate(dut, [put_testbench, get_testbench], name = "ss_avg2")
    
    
    # Raster Scanner
    def test_raster_scanner(self):

        #TODO: add a test that covers fractional steps
        dut = RasterScanner()

        async def put_testbench(ctx):
            await put_stream(ctx, dut.roi_stream, {
                "x_start": 5, "x_count": 3, "x_step": 0x2_00,
                "y_start": 9, "y_count": 2, "y_step": 0x5_00,
            })
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 1})
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 2})
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 3})
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 7})
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 8})
            await put_stream(ctx, dut.dwell_stream, {"dwell_time": 9})

        async def get_testbench(ctx):
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 5, "dac_y_code": 9,  "dwell_time": 1})
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 7, "dac_y_code": 9,  "dwell_time": 2})
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 9, "dac_y_code": 9,  "dwell_time": 3})
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 5, "dac_y_code": 14, "dwell_time": 7})
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 7, "dac_y_code": 14, "dwell_time": 8})
            await get_stream(ctx, dut.dac_stream, {"dac_x_code": 9, "dac_y_code": 14, "dwell_time": 9})
            assert ctx.get(dut.dac_stream.valid) == 0
            assert ctx.get(dut.roi_stream.ready) == 1

        self.simulate(dut, [get_testbench,put_testbench], name = "raster_scanner")  

    # Command Parser
    def test_command_parser(self):
        dut = CommandParser()

        def test_cmd(command:BaseCommand, name:str="cmd"):
            logger.debug(f"testing {command.fieldstr}")
            async def put_testbench(ctx):
                for byte in bytes(command):
                    await put_stream(ctx, dut.usb_stream, byte)
            async def get_testbench(ctx):
                d = command.as_dict()
                logger.debug(f"{d=}")
                await get_stream(ctx, dut.cmd_stream, d, 
                    timeout_steps=len(command)*2 + 2)
                await ctx.tick()
                self.assertEqual(ctx.get(dut.cmd_stream.valid),0)
            self.simulate(dut, [get_testbench,put_testbench], name="parse_" + name)  
        
        test_cmd(SynchronizeCommand(cookie=1234, raster=True, output=OutputMode.NoOutput),"cmd_sync")
        
        test_cmd(AbortCommand(), "cmd_abort")
        
        test_cmd(FlushCommand(),"cmd_flush")
        
        test_cmd(ExternalCtrlCommand(enable=True), "cmd_extctrlenable")
        
        test_cmd(BeamSelectCommand(beam_type=BeamType.Electron),"cmd_selectebeam")

        test_cmd(BlankCommand(enable=True, inline=False),"cmd_blank")

        test_cmd(DelayCommand(delay=960),"cmd_delay")

        x_range = DACCodeRange(start=5, count=2, step=0x2_00)
        y_range = DACCodeRange(start=9, count=1, step=0x5_00)

        test_cmd(RasterRegionCommand(x_range=x_range, y_range=y_range), "cmd_rasterregion")

        test_cmd(RasterPixelRunCommand(length=5, dwell_time= 6),"cmd_rasterpixelrun")
        

        test_cmd(RasterPixelFreeRunCommand(dwell_time = 10), "cmd_rasterpixelfreerun")


        test_cmd(VectorPixelCommand(x_coord=4, y_coord=5, dwell_time= 6),"cmd_vectorpixel")

        test_cmd(VectorPixelCommand(x_coord=4, y_coord=5, dwell_time= 1),"cmd_vectorpixelmin")
    
        def test_raster_pixels_cmd():
            command = ArrayCommand(cmdtype = CmdType.RasterPixel, array_length = 5)
            dwells = [1,2,3,4,5,6]
            async def put_testbench(ctx):
                for byte in bytes(command):
                    await put_stream(ctx, dut.usb_stream, byte)
                for dwell in dwells:
                    for byte in struct.pack(">H", dwell):
                        await put_stream(ctx, dut.usb_stream, byte)
            async def get_testbench(ctx):
                for dwell in dwells:
                    await get_stream(ctx, dut.cmd_stream, RasterPixelCommand(dwell_time=dwell).as_dict(), timeout_steps=len(command)*2 + len(dwells)*2 + 2)
                    self.assertEqual(ctx.get(dut.cmd_stream.valid),0)
                self.assertEqual(ctx.get(dut.is_started),1)
            self.simulate(dut, [get_testbench,put_testbench], name="parse_cmd_rasterpixel")  
        
        test_raster_pixels_cmd()

    # Command Executor
    def test_command_executor_individual(self):
        dut = CommandExecutor()

        def test_sync_exec():
            cookie = 1234

            async def put_testbench(ctx):
                await put_stream(ctx, dut.cmd_stream, 
                        SynchronizeCommand(raster=True, output=OutputMode.NoOutput, cookie=cookie).as_dict())
            
            async def get_testbench(ctx):
                await get_stream(ctx, dut.img_stream, 65535) # FFFF
                await get_stream(ctx, dut.img_stream, cookie)
        
            self.simulate(dut, [put_testbench, get_testbench], name = "exec_sync")  

        def test_rasterregion_exec():

            async def put_testbench(ctx):
                await put_stream(ctx, dut.cmd_stream, 
                    RasterRegionCommand(x_range=DACCodeRange(start=5, count=2, step=0x2_00),
                    y_range=DACCodeRange(start=9, count=1, step=0x5_00)).as_dict())
            async def get_testbench(ctx):
                data = await ctx.tick().sample(dut.raster_scanner.roi_stream.payload).until(dut.raster_scanner.roi_stream.valid == 1)
                logger.debug(f"{data=}")
                payload = {"x_start": 5,
                            "x_count": 2,
                            "x_step": 0x2_00,
                            "y_start": 9,
                            "y_count": 1,
                            "y_step": 0x5_00}
                wrapped_payload = dut.raster_scanner.roi_stream.payload.shape().const(payload)
                assert data[0] == wrapped_payload,  f"{prettier_diff(data[0], payload)}"

            self.simulate(dut, [get_testbench,put_testbench], name = "exec_rasterregion")  

        def test_rasterpixel_exec():

            async def put_testbench(ctx):
                await put_stream(ctx, dut.cmd_stream, 
                    RasterPixelCommand(dwell_time=5).as_dict())
            async def get_testbench(ctx):
                data = await ctx.tick().sample(dut.raster_scanner.dwell_stream.payload).until(dut.raster_scanner.dwell_stream.valid == 1)
                payload = {
                        "dwell_time": 5,
                        "blank": {
                            "enable": 0,
                            "request": 0
                        }}
                wrapped_payload = dut.raster_scanner.dwell_stream.payload.shape().const(payload)
                assert data[0] == wrapped_payload,  f"{prettier_diff(data[0], payload)}"
                
            self.simulate(dut, [get_testbench,put_testbench], name = "exec_rasterpixel")  
        
        def test_rasterpixelrun_exec():

            async def put_testbench(ctx):
                await put_stream(ctx, dut.cmd_stream, 
                    RasterRegionCommand(x_range=DACCodeRange(start=5, count=2, step=0x2_00),
                    y_range=DACCodeRange(start=9, count=2, step=0x5_00)).as_dict())
                await put_stream(ctx, dut.cmd_stream, 
                RasterPixelRunCommand(length=2, dwell_time=1).as_dict())

            async def get_testbench(ctx):
                data = await ctx.tick().sample(dut.raster_scanner.roi_stream.payload).until(dut.raster_scanner.roi_stream.valid == 1)
                logger.debug(f"{data=}")
                payload = {"x_start": 5,
                            "x_count": 2,
                            "x_step": 0x2_00,
                            "y_start": 9,
                            "y_count": 2,
                            "y_step": 0x5_00}
                wrapped_payload = dut.raster_scanner.roi_stream.payload.shape().const(payload)
                assert data[0] == wrapped_payload,  f"{prettier_diff(data[0], payload)}"
                async def get_stream(ctx, stream, payload):
                    res = await ctx.tick().sample(stream.payload).until(dut.supersampler.dac_stream.ready & dut.supersampler.dac_stream.valid)
                    wrapped_payload = stream.payload.shape().const(payload)
                    assert res[0] == wrapped_payload,  f"{prettier_diff(res[0], payload)}"

                for _ in range(2):
                    await get_stream(ctx, dut.raster_scanner.dwell_stream,  {
                        "dwell_time": 1,
                        "blank": {
                            "enable": 0,
                            "request": 0
                        }})

            self.simulate(dut, [get_testbench,put_testbench], name = "exec_rasterpixelrun")  
        
        from obi.commands.low_level_commands import RasterPixelFillCommand
        def test_rasterpixelfill_exec():
            async def put_testbench(ctx):
                await put_stream(ctx, dut.cmd_stream, 
                    RasterRegionCommand(x_range=DACCodeRange(start=5, count=2, step=0x2_00),
                    y_range=DACCodeRange(start=9, count=2, step=0x5_00)).as_dict())
                await put_stream(ctx, dut.cmd_stream, 
                RasterPixelFillCommand(dwell_time=1).as_dict())

            async def get_testbench(ctx):
                data = await ctx.tick().sample(dut.raster_scanner.roi_stream.payload).until(dut.raster_scanner.roi_stream.valid == 1)
                logger.debug(f"{data=}")
                payload = {"x_start": 5,
                            "x_count": 2,
                            "x_step": 0x2_00,
                            "y_start": 9,
                            "y_count": 2,
                            "y_step": 0x5_00}
                wrapped_payload = dut.raster_scanner.roi_stream.payload.shape().const(payload)
                assert data[0] == wrapped_payload,  f"{prettier_diff(data[0], payload)}"
                async def get_stream(ctx, stream, payload):
                    res = await ctx.tick().sample(stream.payload).until(dut.supersampler.dac_stream.ready & dut.supersampler.dac_stream.valid)
                    wrapped_payload = stream.payload.shape().const(payload)
                    assert res[0] == wrapped_payload,  f"{prettier_diff(res[0], payload)}"
                for _ in range(2*2):
                    await get_stream(ctx, dut.raster_scanner.dwell_stream,  {
                        "dwell_time": 1,
                        "blank": {
                            "enable": 0,
                            "request": 0
                        }})
            self.simulate(dut, [get_testbench,put_testbench], name = "exec_rasterpixelfill")  


        test_sync_exec()
        test_rasterregion_exec()
        test_rasterpixel_exec()
        test_rasterpixelrun_exec()
        test_rasterpixelfill_exec()

    def test_blanking(self):
        dut = CommandExecutor()

        async def async_unblank(ctx): #assumes starting from default or blanked state
            ctx.set(dut.cmd_stream.payload, BlankCommand(enable=False, inline=False).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick()
            ctx.set(dut.cmd_stream.valid, 0)
            await ctx.tick("dac_clk")
            assert ctx.get(dut.blank_enable) == 0

        async def async_blank(ctx): #assumes starting from an unblanked state
            ctx.set(dut.cmd_stream.payload, BlankCommand(enable=True, inline=False).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick()
            ctx.set(dut.cmd_stream.valid, 0)
            await ctx.tick("dac_clk")
            assert ctx.get(dut.blank_enable) == 1
        
        async def sync_unblank(ctx): #assumes starting from default or blanked state
            ctx.set(dut.cmd_stream.payload, BlankCommand(enable=False, inline=True).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick().until(dut.cmd_stream.ready == 0)
            assert ctx.get(dut.blank_enable) == 1 #shouldn't be unblanked yet
            ctx.set(dut.cmd_stream.payload, 
                VectorPixelCommand(x_coord=1, y_coord=1, dwell_time=1).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick().until(dut.cmd_stream.ready == 0)
            ctx.set(dut.cmd_stream.valid, 0)
            await ctx.tick().until(dut.supersampler.super_dac_stream.valid == 1) # bus controller recieves dac codes
            await ctx.tick("dac_clk").repeat(2) # dac codes are latched
            assert ctx.get(dut.blank_enable) == 0

        async def sync_blank(ctx): #assumes starting from an unblanked state
            ctx.set(dut.cmd_stream.payload, BlankCommand(enable=True, inline=True).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick().until(dut.cmd_stream.ready == 0)
            assert ctx.get(dut.blank_enable) == 0 #shouldn't be blanked yet
            ctx.set(dut.cmd_stream.payload, VectorPixelCommand(x_coord=2, y_coord=2, dwell_time=1).as_dict())
            ctx.set(dut.cmd_stream.valid, 1)
            await ctx.tick().until(dut.cmd_stream.ready == 0)
            ctx.set(dut.cmd_stream.valid, 0)
            await ctx.tick().until(dut.supersampler.super_dac_stream.valid == 1) # bus controller recieves dac codes
            await ctx.tick("dac_clk").repeat(2) # dac codes are latched
            assert ctx.get(dut.blank_enable) == 1
        
        async def test_seq_1(ctx):
            await async_unblank(ctx)
            await async_blank(ctx)
            await async_unblank(ctx)
            await sync_blank(ctx)
            await sync_unblank(ctx)
            await async_blank(ctx)
            #await sync_unblank(ctx)
        
        self.simulate(dut, [async_unblank], name="async_unblank")
        self.simulate(dut, [sync_unblank], name="sync_unblank")
        self.simulate(dut, [test_seq_1], name="blank_seq_1")
    
    def test_command_executor_sequences(self):
        BUS_CYCLES = 6 #combined ADC and DAC latching cycles
        class TestCommand:
            response = []
            def __init_subclass__(cls, command:BaseCommand):
                cls.command_cls = command
            def __init__(self, **kwargs):
                self.command = self.command_cls(**kwargs)
            def __repr__(self):
                return self.command.__repr__()
            @property
            def exec_cycles(self):
                return len(self.response)*BUS_CYCLES
            async def put_testbench(self, ctx, dut):
                logger.debug(f"put_testbench: {self}")
                await put_stream(ctx, dut.cmd_stream, self.command.as_dict(), timeout_steps=self.exec_cycles+100)
            async def get_testbench(self, ctx, dut):
                logger.debug(f"get_testbench: {self}")
                if len(self.response) > 0:
                    n = 0
                    logger.debug(f"getting {len(self.response)} responses")
                    for n in range(len(self.response)):
                        await get_stream(ctx, dut.img_stream, self.response[n], timeout_steps=self.exec_cycles+100)
                        n += 1
                        logger.debug(f"got {n}/{len(self.response)} responses")
                    logger.debug(f"got all {len(self.response)} responses")
                else:
                    logger.debug("get_testbench: no response expected")
                    pass
        
        class TestCommandSequence:
            def __init__(self):
                self.dut =  CommandExecutor(ext_switch_delay=10)
                self.put_testbenches = []
                self.get_testbenches = []
        
            def add(self, command: TestCommand):
                async def put_bench(ctx):
                    await command.put_testbench(ctx, self.dut)
                self.put_testbenches.append(put_bench)

                async def get_bench(ctx):
                    await command.get_testbench(ctx, self.dut)
                self.get_testbenches.append(get_bench)
            
            async def put_testbench(self, ctx):
                for testbench in self.put_testbenches:
                    await testbench(ctx)
            
            async def get_testbench(self, ctx):
                for testbench in self.get_testbenches:
                    await testbench(ctx)
    
        class TestSyncCommand(TestCommand, command=SynchronizeCommand):
            @property
            def response(self):
                return [65535, self.command.cookie] # FFFF, cookie
        
        class TestFlushCommand(TestCommand, command=FlushCommand):
            pass

        class TestExternalCtrlCommand(TestCommand, command = ExternalCtrlCommand):
            async def put_testbench(self, ctx, dut):
                await super().put_testbench(ctx, dut)
                for _ in range(dut.ext_switch_delay):
                    await ctx.tick()
                if not ctx.get(dut.is_executing) == 0:
                    await ctx.tick()
                assert ctx.get(dut.ext_ctrl_enable) == self.command.enable
            
            async def get_testbench(self, ctx, dut):
                for _ in range(dut.ext_switch_delay):
                    await ctx.tick()
                await super().get_testbench(ctx, dut)
            
        class TestBeamSelectCommand(TestCommand, command = BeamSelectCommand):
            async def put_testbench(self, ctx, dut):
                await super().put_testbench(ctx, dut)
                if not ctx.get(dut.is_executing) == 0:
                    await ctx.tick()
                assert ctx.get(dut.beam_type) == self.command.beam_type

        class TestDelayCommand(TestCommand, command = DelayCommand):
            @property
            def exec_cycles(self):
                return self.command.delay

            async def put_testbench(self, ctx, dut):
                await super().put_testbench(ctx, dut)
                for _ in range(self.command.delay):
                    await ctx.tick()
            
            async def get_testbench(self, ctx, dut):
                for _ in range(self.command.delay):
                    await ctx.tick()
                await super().get_testbench(ctx, dut)
        
        class TestRasterRegionCommand(TestCommand, command=RasterRegionCommand):
            async def put_testbench(self, ctx, dut):
                await super().put_testbench(ctx, dut)
                region = ctx.get(dut.raster_scanner.roi_stream.payload)
                expected_region = {
                    "x_start": self.command.x_start,
                    "x_count": self.command.x_count,
                    "x_step": self.command.x_step,
                    "y_start": self.command.y_start,
                    "y_count": self.command.y_count,
                    "y_step": self.command.y_step}
                wrapped = dut.raster_scanner.roi_stream.payload.shape().const(expected_region)
                assert wrapped == region, f"{prettier_diff(region, expected_region)}"
        
        class TestRasterPixelRunCommand(TestCommand, command=RasterPixelRunCommand):
            @property
            def response(self):
                return [0]*(self.command.length+1)
            @property
            def exec_cycles(self):
                return self.command.dwell_time*self.command.length*BUS_CYCLES

            async def put_testbench(self, ctx, dut):
                await super().put_testbench(ctx, dut)
                dwell = ctx.get(dut.raster_scanner.dwell_stream.payload.dwell_time)
                assert dwell == self.command.dwell_time, f"{dwell} != {self.command.dwell_time}"
        
        class TestRasterPixelFreeRunCommand(TestCommand, command=RasterPixelFreeRunCommand):
            def __init__(self, *, dwell_time, test_samples):
                super().__init__(dwell_time = dwell_time)
                self.test_samples = test_samples

            @property
            def response(self):
                return [0]*self.test_samples
            @property
            def exec_cycles(self):
                return self.command.dwell_time*self.test_samples*BUS_CYCLES
        
            async def put_testbench(self, ctx, dut):
                logger.debug(f"put_testbench: {self}")
                await super().put_testbench(ctx, dut)
                n = 0
                logger.debug(f"extending put_testbench for {self.test_samples} samples")
                while True:
                    if n == self.test_samples:
                        break
                    await ctx.tick().until(dut.supersampler.dac_stream.ready == 1)
                    n += 1
                    logger.debug(f"{n}/{self.test_samples} valid samples")
                    

        class TestVectorPixelCommand(TestCommand, command=VectorPixelCommand):
            @property
            def response(self):
                return [0]
            @property
            def exec_cycles(self):
                return self.command.dwell_time*BUS_CYCLES
        


        def test_exec_1():
            test_seq = TestCommandSequence()
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestSyncCommand(cookie=505, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestRasterRegionCommand(x_range=DACCodeRange(start=5, count=3, step=0x2_00),
                                                y_range=DACCodeRange(start=9, count=3, step=0x5_00)))
            test_seq.add(TestRasterPixelRunCommand(length=6, dwell_time=1))
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestRasterPixelFreeRunCommand(dwell_time=1, test_samples = 6))
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestSyncCommand(cookie=102, raster=True, output=OutputMode.SixteenBit))

            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_1")

        def test_exec_2():
            test_seq = TestCommandSequence()
            test_seq.add(TestExternalCtrlCommand(enable=True))
            test_seq.add(TestBeamSelectCommand(beam_type=BeamType.Electron))
            test_seq.add(TestDelayCommand(delay=960))
            test_seq.add(TestSyncCommand(cookie=505, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestRasterRegionCommand(x_range=DACCodeRange(start=5, count=2, step=0x2_00),
                                                y_range=DACCodeRange(start=9, count=3, step=0x5_00)))
            test_seq.add(TestRasterPixelRunCommand(length=5, dwell_time=1))

            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_2")
        
        def test_exec_3():
            test_seq = TestCommandSequence()
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestSyncCommand(cookie=505, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestExternalCtrlCommand(enable=True))
            test_seq.add(TestBeamSelectCommand(beam_type=BeamType.Electron))
            test_seq.add(TestDelayCommand(delay=960))
            test_seq.add(TestRasterRegionCommand(x_range=DACCodeRange(start=5, count=10, step=0x2_00),
                                                y_range=DACCodeRange(start=9, count=2, step=0x5_00)))
            test_seq.add(TestRasterPixelFreeRunCommand(dwell_time=1, test_samples=20))
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))
            test_seq.add(TestRasterRegionCommand(x_range=DACCodeRange(start=5, count=10, step=0x2_00),
                                                y_range=DACCodeRange(start=9, count=2, step=0x5_00)))
            test_seq.add(TestRasterPixelFreeRunCommand(dwell_time=1, test_samples=20))
            test_seq.add(TestExternalCtrlCommand(enable=True))
            test_seq.add(TestBeamSelectCommand(beam_type=BeamType.Electron))
            test_seq.add(TestDelayCommand(delay=960))
            test_seq.add(TestSyncCommand(cookie=502, raster=True, output=OutputMode.SixteenBit))

            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_3")
        
        def test_exec_4():
            test_seq = TestCommandSequence()
            test_seq.add(TestSyncCommand(cookie=502, raster=False, output=OutputMode.SixteenBit))
            test_seq.add(TestVectorPixelCommand(x_coord=100, y_coord=244, dwell_time=3))
            test_seq.add(TestVectorPixelCommand(x_coord=90, y_coord=144, dwell_time=2))
            test_seq.add(TestVectorPixelCommand(x_coord=110, y_coord=2004, dwell_time=5))
            test_seq.add(TestSyncCommand(cookie=502, raster=False, output=OutputMode.SixteenBit))

            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_4")
        
        def test_exec_5():
            test_seq = TestCommandSequence()
            test_seq.add(TestSyncCommand(cookie=502, raster=False, output=OutputMode.NoOutput))
            test_seq.add(TestFlushCommand())
            for n in range(100):
                test_seq.add(TestVectorPixelCommand(x_coord=1, y_coord=1, dwell_time=1))
                test_seq.add(TestVectorPixelCommand(x_coord=16384, y_coord=16384, dwell_time=1))
            test_seq.add(TestSyncCommand(cookie=502, raster=False, output=OutputMode.NoOutput))

            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_5")
        
        def test_exec_6():
            test_seq = TestCommandSequence()
            test_seq.add(TestExternalCtrlCommand(enable=True))
            test_seq.add(TestBeamSelectCommand(beam_type=BeamType.Ion))
            test_seq.add(TestVectorPixelCommand(x_coord=1, y_coord=1, dwell_time=1))
            self.simulate(test_seq.dut, [test_seq.put_testbench, test_seq.get_testbench], name="exec_6")


        test_exec_1()
        test_exec_2()
        test_exec_3()
        test_exec_4()
        test_exec_5()
        test_exec_6()

    @unittest.skip("Skipped applet test case")
    def test_all(self):
        #TODO: Fix this simulation
        from amaranth import Module
        from obi.applet.open_beam_interface import OBIApplet
        from glasgow.applet import GlasgowAppletTestCase, synthesis_test, applet_simulation_test
        from .board_sim import OBI_Board

        class OBIApplet_TestCase(GlasgowAppletTestCase, applet = OBIApplet):
            @synthesis_test
            def test_build(self):
                self.assertBuilds(args=["--pins-ebeam-scan-enable", "1", "--xflip", "--yflip", "--rotate90"])
            
            def setup_test(self):
                self.build_simulated_applet()

            def setup_x_loopback(self):
                self.build_simulated_applet()
                obi_subtarget = self.applet.mux_interface._subtargets[0]
                logger.debug(vars(obi_subtarget))
                m = Module()
                m.submodules["board"] = board = OBI_Board()
                m.d.comb += [
                            obi_subtarget.data.i.eq(board.a_latch_chip.q),
                            board.x_latch_chip.d.eq(obi_subtarget.data.o),
                            board.y_latch_chip.d.eq(obi_subtarget.data.o),
                            board.a_adc_chip.a.eq(board.x_dac_chip.a),
                            board.x_latch.eq(obi_subtarget.control.x_latch.o),
                            board.y_latch.eq(obi_subtarget.control.y_latch.o),
                            board.a_latch.eq(obi_subtarget.control.a_latch.o),
                            board.a_enable.eq(obi_subtarget.control.a_enable.o),
                            board.a_clock.eq(obi_subtarget.control.a_clock.o),
                            board.d_clock.eq(obi_subtarget.control.d_clock.o),
                            board.adc_input.eq(board.x_dac_chip.a)
                            ]
                self.target.add_submodule(m)

            ## tests that actually test against output
            
            @applet_simulation_test("setup_test")
            async def test_sync_cookie(self):
                iface = await self.run_simulated_applet()
                #await iface.write(bytes([0, 123, 234])) # sync, cookie, raster_mode
                await iface.write(SynchronizeCommand(cookie=123*256 + 234, raster=False, output=OutputMode.SixteenBit))
                self.assertEqual(await iface.read(4), bytes([0xFF, 0xFF, 123, 234])) # FF, FF, cookie
            
            @applet_simulation_test("setup_x_loopback")
            async def test_loopback_raster(self):
                iface = await self.run_simulated_applet()
                await iface.write(SynchronizeCommand(cookie=123*256 + 234, raster=True, output=OutputMode.SixteenBit))
                self.assertEqual(await iface.read(4), bytes([0xFF, 0xFF, 123, 234])) # FF, FF, cookie
                x_range = DACCodeRange(start=0, count=5, step=256) #step = 1 DAC code
                y_range = DACCodeRange(start=5, count=10, step=256)
                await iface.write(RasterRegionCommand(x_range=x_range, y_range=y_range))
                await iface.write(RasterPixelRunCommand(length=25, dwell_time=2))
                res = array.array('H',[x for x in range(5)]*5)
                res.byteswap()
                #await iface.read(50)
                self.assertEqual(await iface.read(50), bytes(res))
            
            @applet_simulation_test("setup_x_loopback")
            async def test_benchmark(self):
                iface = await self.run_simulated_applet()
                output_mode = 2 #no output
                raster_mode = 0 #no raster
                mode = int(output_mode<<1 | raster_mode)
                sync_cmd = struct.pack('>BHB', 0, 123, mode)
                flush_cmd = struct.pack('>B', 2)
                await iface.write(sync_cmd)
                await iface.write(flush_cmd)
                # await iface.flush()
                commands = bytearray()
                for _ in range(10):
                    await iface.write(VectorPixelCommand(x_coord=4, y_coord=4, dwell_time=1))
                    await iface.write(VectorPixelCommand(x_coord=16380, y_coord=16380, dwell_time=1))
                    await iface.write(VectorPixelCommand(x_coord=4, y_coord=16380, dwell_time=1))
                    await iface.write(VectorPixelCommand(x_coord=16380, y_coord=4, dwell_time=1))
            
            @applet_simulation_test("setup_x_loopback")
            async def test_loopback_vector(self):
                iface = await self.run_simulated_applet()
                commands = bytearray()
                commands.extend(bytes(SynchronizeCommand(output=OutputMode.SixteenBit, raster=False, cookie=123*256+234)))
                commands.extend(bytes(FlushCommand()))
                for n in range(1,11):
                    commands.extend(bytes(VectorPixelCommand(x_coord=n, y_coord=n, dwell_time=1)))
                await iface.write(commands)
                self.assertEqual(await iface.read(4), bytes([0xFF, 0xFF, 123, 234])) # FF, FF, cookie
                res = array.array('H',[x for x in range(1,11)])
                res.byteswap()
                self.assertEqual(await iface.read(20), bytes(res))
            
            @applet_simulation_test("setup_x_loopback")
            async def test_loopback_multimode(self):
                iface = await self.run_simulated_applet()
                # await iface.flush()
                commands = bytearray()
                #commands.extend(bytes(SynchronizeCommand(output=OutputMode.SixteenBit, raster=False, cookie=123*256+234)))
                for n in range(1,11):
                    commands.extend(bytes(VectorPixelCommand(x_coord=n, y_coord=n, dwell_time=1)))
                x_range = DACCodeRange(start=0, count=5, step=256) #step = 1 DAC code
                y_range = DACCodeRange(start=5, count=10, step=256)
                commands.extend(bytes(RasterRegionCommand(x_range=x_range, y_range=y_range)))
                commands.extend(bytes(RasterPixelRunCommand(length=20, dwell_time=2)))
                commands.extend(bytes(VectorPixelCommand(x_coord=4, y_coord=7, dwell_time=2)))
                commands.extend(bytes(RasterPixelRunCommand(length=4, dwell_time=2)))
                commands.extend(bytes(FlushCommand()))
                await iface.write(commands)
                #self.assertEqual(await iface.read(4), bytes([0xFF, 0xFF, 123, 234])) # FF, FF, cookie
                res = array.array('H',[x for x in range(1,11)])
                res.byteswap()
                self.assertEqual(await iface.read(20), bytes(res))
                #await iface.write(SynchronizeCommand(cookie=123*256 + 234, raster=True, output=OutputMode.SixteenBit))
                #self.assertEqual(await iface.read(4), bytes([0xFF, 0xFF, 123, 234])) # FF, FF, cookie
                res = array.array('H',[x for x in range(5)]*4)
                res.byteswap()
                self.assertEqual(await iface.read(40), bytes(res))
            
            ## tests that are more for observation
            @applet_simulation_test("setup_test", args=["--pin-ext-ibeam-scan-enable", "0", "--pin-ext-ibeam-scan-enable-2", "1"])
            async def test_vector_blank(self):
                iface = await self.run_simulated_applet()
                await iface.write(SynchronizeCommand(cookie=4, output=2, raster=0))
                await iface.write(BlankCommand(enable=True, inline=False))
                await iface.write(ExternalCtrlCommand(enable=True))
                await iface.write(BeamSelectCommand(beam_type=BeamType.Ion))
                await iface.write(DelayCommand(delay=10))
                await iface.write(BlankCommand(enable=False, inline=False))
                for n in range(1,3):
                    await iface.write(VectorPixelCommand(x_coord=n, y_coord=n, dwell_time=1))
                await iface.write(VectorPixelCommand(x_coord=7, y_coord=7, dwell_time=3))
                await iface.write(BlankCommand(enable=True, inline=False))
                await iface.write(DelayCommand(delay=3))
                await iface.write(BlankCommand(enable=False, inline=False))
                await iface.write(VectorPixelCommand(x_coord=1, y_coord=1, dwell_time=1))
                await iface.write(BlankCommand(enable=True, inline=False))
                await iface.write(SynchronizeCommand(cookie=4, output=2, raster=0))
                await iface.read(6)


            @applet_simulation_test("setup_x_loopback", args=["--out_only"])
            async def test_vector_delay(self):
                iface = await self.run_simulated_applet()
                await iface.write(VectorPixelCommand(x_coord=1, y_coord=1, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=2))
                await iface.write(VectorPixelCommand(x_coord=2, y_coord=2, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=3))
                await iface.write(VectorPixelCommand(x_coord=3, y_coord=3, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=4))
                await iface.write(VectorPixelCommand(x_coord=4, y_coord=4, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=5))
                await iface.write(VectorPixelCommand(x_coord=5, y_coord=5, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=6))
                await iface.write(VectorPixelCommand(x_coord=6, y_coord=6, dwell_time=6))
                await iface.write(InlineDelayCommand(delay=7))
                await iface.write(SynchronizeCommand(cookie=4, output=2, raster=0))
                await iface.read(4)
                
            
        test_case = OBIApplet_TestCase()
        test_case.setUp()
        test_case.test_build()
        test_case.test_sync_cookie()
        test_case.test_benchmark()
        test_case.test_vector_blank()
        test_case.test_loopback_raster()
        test_case.test_loopback_vector()
        #test_case.test_loopback_multimode()
