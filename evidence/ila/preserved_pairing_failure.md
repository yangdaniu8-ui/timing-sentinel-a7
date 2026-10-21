# Preserved pairing failure

`ila_live12_case02` has a complete, audited task-4 frozen record, but its exported ILA CSV repeats task-3's A tick (39677982981), not task-4's A tick (40670616817). It is NOT counted as an ILA pass. Original files remain unchanged.

The Vivado log shows no new arm-command execution between the first pair upload and the second upload; upload reports `ILA core is not armed. Uploading previously captured data`. Identical tool-command reuse is the suspected orchestration cause, not a proven FPGA defect. Subsequent arm requests must carry a unique case tag and require fresh ARMED status before measurement. Pair comparisons remain strict; no failed data is relabelled.
