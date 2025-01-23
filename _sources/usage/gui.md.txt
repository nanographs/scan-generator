# OBI GUI
All of the following instructions should be run in the directory `Open-Beam-Interface/software`.
## Installation
Install the dependencies for the GUI:
```
pdm lock -G gui
pdm install -G gui
```

## Running the GUI
Open the launcher:
```
pdm run launch
```
Click the "Start" buttons for both the server and the GUI. You can launch these in either order, but the GUI controls won't do anything until the server is up and running.