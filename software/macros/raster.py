from commands import *
import asyncio

class RasterScanCommand(BaseCommand):
    def __init__(self, x_range: DACCodeRange, y_range: DACCodeRange, dwell_time: int, cookie: int,
        output_mode:OutputMode=OutputMode.SixteenBit):
        self._x_range = x_range
        self._y_range = y_range
        self._dwell = dwell_time
        self._cookie = cookie
        self._output_mode = output_mode
    
    def _iter_chunks(self, latency=65536*65536):
        commands = bytearray()

        def append_command(pixel_count):
            array_count = pixel_count//65536
            remainder_pixel_count = pixel_count%65536
            assert array_count < 65536, "can't handle more than 65536x65536 points"
            self._logger.debug(f"{array_count=}, {remainder_pixel_count=}")
            if array_count > 0:
                commands.extend(bytes(ArrayCommand(cmdtype=CmdType.RasterPixelRun, array_length=array_count)))
                chunk = array.array('H', [self._dwell]*array_count)
                if not BIG_ENDIAN: # there is no `array.array('>H')`
                    chunk.byteswap()
                commands.extend(chunk.tobytes())
            if remainder_pixel_count > 0:
                commands.extend(bytes(RasterPixelRunCommand(dwell_time = self._dwell, length = remainder_pixel_count)))

        pixel_count = 0
        total_dwell = 0
        for _ in range(self._x_range.count * self._y_range.count):
            pixel_count += 1
            total_dwell += self._dwell
            if total_dwell >= latency:
                append_command(pixel_count)
                yield(commands, pixel_count)
                commands = bytearray()
                pixel_count = 0
                total_dwell = 0
        if pixel_count > 0:
            append_command(pixel_count)
            yield(commands, pixel_count)

    @BaseCommand.log_transfer
    async def transfer(self, stream, latency: int):
        MAX_PIPELINE = 32

        tokens = MAX_PIPELINE
        token_fut = asyncio.Future()

        async def sender():
            nonlocal tokens
            for commands, pixel_count in self._iter_chunks(latency):
                self._logger.debug(f"sender: tokens={tokens}")
                print(f"sender: {len(commands)=}, {pixel_count=}")
                if tokens == 0:
                    await FlushCommand().transfer(stream)
                    await token_fut
                await stream.write(commands)
                tokens -= 1
                await asyncio.sleep(0)
            await FlushCommand().transfer(stream)

        await SynchronizeCommand(cookie=self._cookie, raster=True, output = self._output_mode).transfer(stream)
        await RasterRegionCommand(x_range=self._x_range, y_range=self._y_range).transfer(stream)
        asyncio.create_task(sender())

        cookie = await stream.read(4) #just assume these are exactly FFFF + cookie, and discard them
        for commands, pixel_count in self._iter_chunks(latency):
            tokens += 1
            if tokens == 1:
                token_fut.set_result(None)
                token_fut = asyncio.Future()
            self._logger.debug(f"recver: tokens={tokens}")
            yield await self.recv_res(pixel_count, stream, self._output_mode)