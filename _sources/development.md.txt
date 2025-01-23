# Developing OBI

## Editing the documentation
OBI documentation is built with [Sphinx](https://www.sphinx-doc.org/en/master/) and the [MyST](https://myst-parser.readthedocs.io/en/latest/) Markdown parser. This configuration was chosen to combine the advanced autodocumentation features of Sphinx with the lower friction of writing in Markdown.

To build the documentation:
```
pdm run docs
```

To view the documentation locally:
```
pdm run docs_serve
```
The documentation will be available at `localhost:8000`

## Environment management
Currently, we use [PDM](https://pdm-project.org/en/latest/) to install OBI in a virtual environment, so all installs are local and editable.
After [installing](installation.md) OBI, a virtual environment is created in `software/.venv`. 

### PDM Quick Reference
To add a dependency from PyPI:
```
pdm add package
```
To add a dependency from a local file:
```
pdm add path/to/package
```

Examples:
```
pdm add matplotlib
pdm add ~/glasgow/software
```

To add a dependency to a group:
```
pdm add -G group dependency
```

Example:
```
pdm add -G gui pyqtgraph
```

## Running tests
OBI tests run with [unittest](https://docs.python.org/3/library/unittest.html).

To run all tests:
```
pdm run test
```

To run a specific test:
```
pdm run test tests.commands.test_structs -k test_from_resolution
```
This runs the test `DACCodeRangeTest.test_from_resolution()` in `/software/tests/commands/test_structs`.

## Waveform simulations
Gateware simulations, located in `software/tests/gateware/test_open_beam_interface.py`, produce `.vcd` files. I have tried and would recommend the following VCD viewers:
- [GTKWave](https://gtkwave.sourceforge.net) is the most full featured free and open source VCD viewer.
    - I used [randomplum's Homebrew tap](https://github.com/gtkwave/gtkwave/issues/250#issuecomment-1739998393) to get a version built with gtk3 so that it looks better on MacOS
- [WaveTrace](https://www.wavetrace.io) is a VSCode plugin. The free version lets you view 8 waveforms, for a one time payment of $15 you can get unlimited waveforms.
- [Surfer](https://surfer-project.org) is a new open source VCD viewer, you can use it directly in the browser.

Useful GTKWave configurations for viewing simulation outputs are stored in `software/tests/gtkwave`.

