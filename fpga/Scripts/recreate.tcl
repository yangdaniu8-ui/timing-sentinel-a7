# Run only inside a new empty ASCII build directory. No hardware operations.
set export_dir [file dirname [file dirname [file normalize [info script]]]]
if {[regexp {[^\x00-\x7f]} [pwd]]} {error "ASCII build directory required"}
if {[llength [glob -nocomplain *.xpr *.srcs *.runs]]} {error "Refusing existing project output"}
source [file join $export_dir Scripts build.tcl]
# Restore properties not emitted by the generic helper; preserve USR43 settings.
set_property STEPS.SYNTH_DESIGN.ARGS.FLATTEN_HIERARCHY rebuilt [get_runs synth_1]
set_property STEPS.PHYS_OPT_DESIGN.IS_ENABLED true [get_runs impl_1]
set_property AUTO_INCREMENTAL_CHECKPOINT 0 [get_runs synth_1]
set_property top mvp5_top [get_filesets sources_1]
set_property target_simulator XSim [current_project]
foreach f [get_files -of_objects [get_filesets sources_1]] {
    if {[string match tb_* [file tail $f]] || [file tail $f] eq "capture_buffer_reference.v"} {
        set_property USED_IN_SYNTHESIS false $f
        set_property USED_IN_IMPLEMENTATION false $f
    }
}
set_property top tb_qor_top [get_filesets sim_1]
set_property xsim.elaborate.debug_level off [get_filesets sim_1]
set_property xsim.simulate.log_all_signals false [get_filesets sim_1]
set_property INCREMENTAL false [get_filesets sim_1]
set_property xsim.simulate.runtime 201ms [get_filesets sim_1]
set_property -name XSIM.SIMULATE.XSIM.MORE_OPTIONS -value [list -testplusarg VECTORS=[file join $export_dir Sources Data top_vectors.mem]] -objects [get_filesets sim_1]
update_compile_order -fileset sources_1
update_compile_order -fileset sim_1
puts "SOURCE_PROJECT_RECREATED"
