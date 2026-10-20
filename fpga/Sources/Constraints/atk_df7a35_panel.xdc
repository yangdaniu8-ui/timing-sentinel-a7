# Official ATK V2.1 schematic DEVICE page 2 and DaVinci_FPGA_IO.xdc.
# Existing LED pins R2/R3/V2/Y2 are unchanged; no new external wires.
set_property -dict {PACKAGE_PIN P16 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 4} [get_ports beep]
set_property -dict {PACKAGE_PIN T3 IOSTANDARD LVCMOS33} [get_ports mute_key_n]
set_false_path -from [get_ports mute_key_n]
