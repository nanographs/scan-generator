# Installing OBI 
Prerequisites:
- Python >= 3.10
- [PDM](https://pdm-project.org/latest/#installation)

Clone the OBI repository:
```
git clone https://github.com/nanographs/Open-Beam-Interface
```
Navigate to `Open-Beam-Interface/software`:
```
cd Open-Beam-Interface/software
```
Install virtual environment with PDM:
```
pdm install
```

## Using a preexisting Glasgow installation
If you already have Glasgow installed, you can include it in the virtual environment:
```
pdm add /path/to/glasgow/software
```