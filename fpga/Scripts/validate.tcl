# vivado -mode batch -source <bundle>/fpga/Scripts/validate.tcl -tclargs full
# Modes: project (default), regression, full. Always use a fresh ASCII cwd.
set source_dir [file dirname [file normalize [info script]]]
set mode project
if {[llength $argv]} {set mode [lindex $argv 0]}
if {$mode ni {project regression full}} {error "Use project, regression or full"}
source [file join $source_dir recreate.tcl]
if {$mode in {regression full}} {source [file join $source_dir regression.tcl]}
if {$mode eq "full"} {
    launch_runs synth_1 -jobs 4
    wait_on_run synth_1
    if {[get_property STATUS [get_runs synth_1]] ne "synth_design Complete!"} {error "Synthesis incomplete"}
    launch_runs impl_1 -to_step route_design -jobs 4
    wait_on_run impl_1
    if {[get_property STATUS [get_runs impl_1]] ne "route_design Complete!"} {error "Route incomplete"}
    open_run impl_1
    report_utilization -file utilization.rpt
    report_timing_summary -file timing_summary.rpt
    report_drc -file drc.rpt
    set_property BITSTREAM.CONFIG.USR_ACCESS 0x4D565043 [current_design]
    write_checkpoint rebuilt_usr43.dcp
    write_bitstream rebuilt_usr43.bit
}
close_project
puts "SOURCE_VALIDATION_PASS mode=$mode"
