# Generic Scan Selector
[View the KiCAD files with KiCanvas!](https://kicanvas.org/?github=https%3A%2F%2Fgithub.com%2Fnanographs%2FOpen-Beam-Interface%2Ftree%2Fmain%2FHardware%2FPCBs%2FInterface%2520selectors%2520and%2520drivers%2FGeneric%2520Scan%2520Selector)

The Generic Scan Selector is designed to switch between internal and external scan signals. This board is especially useful when retrofitting an Open Beam Interface onto a microscope that does not already have a dedicated external scan connector. To use this board, you need to find a place in the microscope where you can splice into the existing X and Y scan signals before they get sent to the scan coil drivers. 

A RF relay with very good isolation properties between the normally open and normally closed contact is used to select between the internal and external scan signals. This keeps the internal and external scan signals from interfering with each other. The relay used is an [Omron G6K-2P-RF DC5](https://www.digikey.com/en/products/detail/omron-electronics-inc-emc-div/G6K-2P-RF-DC5/5864630). 


This relay is intended to be driven by the Glasgow Digital IO banks (Port A/B). The OBI software is designed to automatically switch between internal and external scan signals when you start and stop scanning from it. You can configure which pins drive this relay by editing the [configuration file](../../config.md). 




![Front](../../_static/Generic_Scan_Selector_Front.jpg)

![Back](../../_static/Generic_Scan_Selector_Back.jpg)


```{figure} ../../_static/Generic_Scan_Selector_Photo.jpg
Photo of Generic Scan Selector in use on JEOL T330. The X and Y micro-coax cables are terminated into the corresponding X and Y holes on the Generic Scan Selector.
```

![Schematic](../../_static/Generic_Scan_Selector_Schematic.jpg)