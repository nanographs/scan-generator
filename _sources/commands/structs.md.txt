# Data Structures and Inputs

## Enums
### Command Types
Set of all available [low-level commands](./low_level_commands.md)

```{eval-rst}
.. autoenum:: obi.commands.structs.CmdType
    :members:
```

### Output Modes
```{eval-rst}
.. autoenum:: obi.commands.structs.OutputMode
    :members:
```

### Beam Types
```{eval-rst}
.. autoenum:: obi.commands.structs.BeamType
    :members:
```

## Numeric types
These classes are provided as convenient wrappers to validate command inputs.
When used as type hints, they are only annotations and are not enforced.
### Numeric base types
#### 14 bit integers
```{eval-rst}
.. autoclass:: obi.commands.structs.u14
```
#### 16 bit integers
```{eval-rst}
.. autoclass:: obi.commands.structs.u16
```
#### 8,8 fractional fixed point values
```{eval-rst}
.. wavedrom:: fp8_8

    {
        "reg": [
            {"bits": 1,  "name": '2^-8', "type": 2},
            {"bits": 1,  "name": '2^-7', "type": 2},
            {"bits": 1,  "name": '2^-6', "type": 2},
            {"bits": 1,  "name": '2^-5', "type": 2},
            {"bits": 1,  "name": '2^-4', "type": 2},
            {"bits": 1,  "name": '2^-3', "type": 2},
            {"bits": 1,  "name": '2^-2', "type": 2},
            {"bits": 1,  "name": '2^-1', "type": 2},
            {"bits": 1,  "name": '2^0'},
            {"bits": 1,  "name": '2^1'},
            {"bits": 1,  "name": '2^2'},
            {"bits": 1,  "name": '2^3'},
            {"bits": 1,  "name": '2^4'},
            {"bits": 1,  "name": '2^5'},
            {"bits": 1,  "name": '2^6'},
            {"bits": 1,  "name": '2^7'},
        ]
    }

.. autoclass:: obi.commands.structs.fp8_8
```


### Scan Parameters
#### Dwell Time
```{eval-rst}
.. autoclass:: obi.commands.structs.DwellTime
```

#### DAC Code Ranges
```{eval-rst}
.. autoclass:: obi.commands.structs.DACCodeRange
```



