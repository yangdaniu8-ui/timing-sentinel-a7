# Vivado Revision Control Export

This directory is organized for git check-in.

## Check In
- Sources/
- Scripts/
- .gitignore
- README.md
- Sources/Constraints/ (includes DFX pblock constraints if present)
- Sources/Checkpoints/ if present and intentionally versioned
- Sources/NoC/ if present

Note: <proj_name>.xpr is intentionally NOT part of this export or check-in --
this skill recreates the project deterministically via Scripts/build.tcl
instead of revision-controlling the XPR itself.

## Do Not Check In
- .Xil/
- *.jou, *.log, vivado*.backup.*
- source_stage.cache/
- source_stage.gen/
- source_stage.hw/
- source_stage.ip_user_files/
- source_stage.runs/
- source_stage.sim/
- source_stage.srcs/

## Recreate
Run Vivado in batch mode with Scripts/build.tcl to recreate the project from the checked-in sources.
