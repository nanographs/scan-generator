# Scripting Automated Acquisitions
:::{admonition} Note: Running Asynchronous Code
:class: note
The Open Beam Interface software makes extensive use of [asyncio](https://docs.python.org/3/library/asyncio.html). Any code that moves data to or from the OBI must be run in an asynchronous context:

```{eval-rst}
    .. code-block::

        import asyncio

        async def main():
            ...

        asyncio.run(main())
```
:::


## Opening a Connection to OBI
### With Direct Access
```{eval-rst}
    .. code-block:: python

        from obi.transfer import GlasgowConnection

        conn = GlasgowConnection()
```
<!-- ```{eval-rst}
    .. literalinclude:: ../../../examples/image_acquisition_direct.py
        :start-at: Open Connection
        :end-before: Create Frame Buffer
``` -->

### With Server Access
In order to run scripts that communicate with OBI via the TCP server, you will need to launch the server with `pdm run server`. The server endpoint can be [configured](../config.md#server-endpoint).

```{eval-rst}
    .. code-block:: python

        from obi.transfer import TCPConnection

        # TCP server must be running at this port
        conn = TCPConnection('localhost', 2224)
```


## Acquiring an Image

Initialize a {py:class}`Frame Buffer <obi.macros.frame_buffer.FrameBuffer>`:

```{eval-rst}
    .. code-block:: python

        fb = FrameBuffer(conn)
```

Define a {py:class}`DAC Range <obi.commands.structs.DACCodeRange>`: In this example, we will use the same DAC range for the X and Y directions.
```{eval-rst}
    .. code-block:: python

        arange = DACCodeRange.from_resolution(2048)
```

Capture a frame with desired parameters:
```{eval-rst}
    .. code-block:: python

        frame = await fb.capture_frame(x_range=arange, y_range=arange, dwell_time=10)
```
In this example, the resulting scan will traverse the full output range of the X and Y DACs in 2048 discrete steps, stopping at each pixel for 10 cycles of 125 ns for a total of 1250 ns. The full scan will take 2.56 ms.

## Putting it all together
```{eval-rst}
    .. literalinclude:: ../../../examples/image_acquisition_direct.py
```