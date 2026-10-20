# Official preserved V2.1 schematic sheets4/9; ALL selected signals BANK14,
# fixed3.3V. Existing STM32 wiring is unchanged. No LCD on shared J3 inputs.
set_property -dict {PACKAGE_PIN R17 IOSTANDARD LVCMOS33 PULLUP TRUE} [get_ports {probe_inputs[0]}]
set_property -dict {PACKAGE_PIN N15 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[1]}]
# CH2..7: J3-35/36/37/38/39/40, respectively. Inputs ONLY.
set_property -dict {PACKAGE_PIN R16 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[2]}]
set_property -dict {PACKAGE_PIN P15 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[3]}]
set_property -dict {PACKAGE_PIN R14 IOSTANDARD LVCMOS33 PULLUP TRUE} [get_ports {probe_inputs[4]}]
set_property -dict {PACKAGE_PIN P14 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[5]}]
set_property -dict {PACKAGE_PIN N14 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[6]}]
set_property -dict {PACKAGE_PIN N13 IOSTANDARD LVCMOS33 PULLDOWN TRUE} [get_ports {probe_inputs[7]}]
# Existing READY: J2-8/P20. CH0 J2-3/R17, CH1 J2-5/N15.
set_property -dict {PACKAGE_PIN P20 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 4} [get_ports target_ready]
set_false_path -from [get_ports {probe_inputs[*]}]
