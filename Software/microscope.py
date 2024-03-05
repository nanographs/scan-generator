from abc import ABCMeta, abstractmethod


class ImageSettings:
    x_resolution: int
    y_resolution: int
    dwell_time: int

class PointSettings:
    x_coord: int
    y_coord: int
    dwell_time: int

class RasterRegionSettings:
    x_start: int
    x_count: int
    y_start: int
    y_count: int

class MicroscopeInterface(metaclass=ABCMeta):
    @abstractmethod
    async def acquire_image(self, settings:ImageSettings):
        pass #return 1 frame

    @abstractmethod
    async def acquire_point(self, settings:PointSettings):
        pass #return 1 point?

