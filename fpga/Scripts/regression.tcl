# Existing USR43 self-checking contracts, re-run against exported source bytes.
# Use in a project recreated by recreate.tcl. No RTL/checker changes.
set source_export [file dirname [file dirname [file normalize [info script]]]]
set run_root [get_property DIRECTORY [current_project]]
set results [file join $run_root regression_results]
if {[file exists $results]} {error "Refuse existing regression evidence"}
file mkdir $results
set idx 0
set summary [open [file join $results summary.txt] w]
try {
    foreach {top duration marker vector} [list \
        tb_mvp5_protocol 101ms {MVP5_PROTOCOL_TB_PASS bytes=9675 packets=118} mvp5_protocol_vectors.mem \
        tb_mvp5_byte_backend 101ms {MVP5_BYTE_BACKEND_TB_PASS bytes=9675 packets=118} mvp5_protocol_vectors.mem \
        tb_selector_contract 11ms {SELECTOR_CONTRACT_PASS frames=21 bytes=8110 calls=13} {} \
        tb_selector_byte_backend 11ms {SELECTOR_BYTE_BACKEND_PASS frames=21 bytes=8110 calls=13} {} \
        tb_qor_top 201ms {QOR_TOP_BYTE_ORACLE_PASS bytes=766 vectors=1114} top_vectors.mem \
        tb_mvp5_capture 31ms {MVP5_CAPTURE_TB_PASS checks=3029 samples=2048} {} \
        tb_boundary_capture 3ms {BOUNDARY_CAPTURE_PASS checks=38} {} \
        tb_capture_equivalence 201ms {CAPTURE_EQUIVALENCE_PASS full_depth=65536 divisors=4 random_cycles=10000 random_captures=200} {} \
        tb_capture_window 1ms {CAPTURE_SPAN_ARITHMETIC_PASS checks=32064} {} \
        tb_fifo_bram 17ms {FIFO_BRAM_CONTRACT_PASS depths=1,2,3,17,256,2048 workers=7 random_each=100000} {}] {
        catch {close_sim -force}
        set simset source_reg_[incr idx]
        create_fileset -simset $simset
        add_files -fileset $simset -norecurse [get_files -of_objects [get_filesets sim_1]]
        current_fileset -simset $simset
        set_property top $top [get_filesets $simset]
        set_property xsim.elaborate.debug_level off [get_filesets $simset]
        set_property XELAB.MT_LEVEL off [get_filesets $simset]
        set_property xsim.simulate.log_all_signals false [get_filesets $simset]
        set_property INCREMENTAL false [get_filesets $simset]
        set_property xsim.simulate.runtime $duration [get_filesets $simset]
        set opts {}
        if {$vector ne ""} {
            set vectors [file join $source_export Sources Data $vector]
            if {![file exists $vectors]} {error "Missing vectors: $vectors"}
            set opts [list -testplusarg VECTORS=$vectors]
        }
        set_property -name XSIM.SIMULATE.XSIM.MORE_OPTIONS -value $opts -objects [get_filesets $simset]
        update_compile_order -fileset $simset
        launch_simulation -mode behavioral -simset $simset
        set sim [file join $run_root source_stage.sim $simset behav xsim]
        foreach name {simulate.log compile.log elaborate.log} {
            file copy [file join $sim $name] [file join $results ${top}_${name}]
        }
        set fh [open [file join $results ${top}_simulate.log] r]
        set transcript [read $fh]
        close $fh
        if {![string match "*$marker*" $transcript] || [regexp -nocase {fatal:|error:|GLOBAL_TIMEOUT} $transcript]} {error "Not PASS: $top"}
        puts $summary "$top $marker"
        flush $summary
    }
    catch {close_sim -force}
    puts $summary "SOURCE_REGRESSION_PASS tests=10"
} finally {close $summary}
set source_regression_result PASS
