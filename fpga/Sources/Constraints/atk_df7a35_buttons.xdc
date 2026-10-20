# Official V2.1 XDC: KEY0=T1, KEY1=U1. Both low-active.
set_property -dict {PACKAGE_PIN T1 IOSTANDARD LVCMOS33} [get_ports start_key_n]
set_property -dict {PACKAGE_PIN U1 IOSTANDARD LVCMOS33} [get_ports release_key_n]
set_false_path -from [get_ports {start_key_n release_key_n}]
