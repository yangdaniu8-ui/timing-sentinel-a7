`timescale 1ns/1ps
module tb_button_demo_control;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,start_n=1,release_n=1,link_idle=1;
    reg [2:0] cap_state=0;reg [31:0] session=0,cfg=1;
    reg done=0;reg [15:0] status=0;
    wire busy,valid;wire [7:0] cmd;wire [15:0] len;wire [799:0] args;
    integer writes=0,commits=0,arms=0,releases=0,checks=0;
    reg fail_write=0,fail_commit=0,fail_arm=0,fail_release=0;
    button_demo_control #(.DEBOUNCE_CYCLES(4),.RELEASE_CYCLES(16)) dut(
        .clk(clk),.rst_n(rst_n),.start_key_n(start_n),.release_key_n(release_n),
        .link_idle(link_idle),.capture_state(cap_state),.session_id(session),.config_id(cfg),
        .command_done(done),.command_status(status),.busy(busy),.command_valid(valid),
        .command(cmd),.argument_length(len),.arguments(args));
    always @(posedge clk) begin
        done<=0;
        if(rst_n && valid) begin
            done<=1;status<=0;
            case(cmd)
                8'h10:begin
                    writes=writes+1;
                    if(len!=100 || args[31:0]!=32'h010f0005 || args[95:80]!=16'h011a ||
                       args[63:32]!=115200) $fatal(1,"canonical config header");
                    if(fail_write) status<=4;
                end
                8'h11:begin commits=commits+1;
                    if(len!=0) $fatal(1,"commit length");
                    if(fail_commit) status<=4;else cfg<=cfg+1;
                end
                8'h20:begin arms=arms+1;
                    if(len!=4 || args[31:0]!=cfg || cap_state!=0) $fatal(1,"arm config/state");
                    if(fail_arm) status<=4;else begin session<=session+1;cap_state<=1;end
                end
                8'h32:begin releases=releases+1;
                    if(len!=4 || args[31:0]!=session || cap_state!=4) $fatal(1,"release session/state");
                    if(fail_release) status<=4;else cap_state<=0;
                end
                default:$fatal(1,"bad local opcode");
            endcase
        end
    end
    task wait_cycles;input integer n;begin repeat(n) @(negedge clk);end endtask
    task press_start;begin start_n=0;wait_cycles(12);start_n=1;wait_cycles(30);end endtask
    task press_release;begin release_n=0;wait_cycles(30);release_n=1;wait_cycles(15);end endtask
    task check;input ok;input [511:0] name;begin
        checks=checks+1;if(!ok) $fatal(1,"BUTTON_CHECK %0s",name);
    end endtask
    initial begin
        wait_cycles(5);rst_n=1;wait_cycles(5);
        check(!busy && !valid,"reset idle");
        start_n=0;wait_cycles(2);start_n=1;wait_cycles(12);
        check(writes==0,"bounce rejected");
        link_idle=0;press_start;check(writes==0,"host reservation blocks start");link_idle=1;
        wait_cycles(20);check(writes==0,"blocked press not deferred");
        start_n=0;wait_cycles(100);
        check(writes==1 && commits==1 && arms==1 && session==1 && cap_state==1,"one ordered start");
        wait_cycles(50);check(arms==1,"held key does not repeat");start_n=1;wait_cycles(10);
        press_start;check(arms==1,"active capture rejects start");
        press_release;check(releases==0,"active capture rejects release");
        cap_state=4;wait_cycles(5);press_start;check(arms==1,"frozen evidence rejects start");
        release_n=0;wait_cycles(10);release_n=1;wait_cycles(10);
        check(releases==0,"short release rejected");
        start_n=0;release_n=0;wait_cycles(35);start_n=1;release_n=1;wait_cycles(10);
        check(releases==0 && arms==1,"simultaneous keys preserve evidence");
        fail_release=1;press_release;check(releases==1 && cap_state==4 && dut.owns_record,"failed release retains ownership");
        fail_release=0;press_release;check(releases==2 && cap_state==0 && !dut.owns_record,"explicit release");
        wait_cycles(20);check(arms==1,"release does not rearm");
        cap_state=4;session=2;press_release;check(releases==2,"host-owned frozen denied");
        cap_state=0;fail_write=1;press_start;check(writes==2 && commits==1 && arms==1 && !busy,"write failure aborts");
        fail_write=0;fail_commit=1;press_start;check(writes==3 && commits==2 && arms==1 && !busy,"commit failure aborts");
        fail_commit=0;fail_arm=1;press_start;check(arms==2 && !dut.owns_record && !busy,"arm failure no ownership");
        fail_arm=0;press_start;check(arms==3 && cap_state==1 && dut.owns_record,"restart after error");
        // Host has since released this record and created a different session.
        cap_state=4;session=session+1;press_release;check(releases==2,"session mismatch denies release");
        rst_n=0;wait_cycles(4);rst_n=1;wait_cycles(5);press_release;
        check(!dut.owns_record && releases==2,"reset drops ownership");
        $display("BUTTON_DEMO_CONTROL_TB_PASS checks=%0d",checks);$finish;
    end
    initial begin #2000000;$fatal(1,"BUTTON_GLOBAL_TIMEOUT");end
endmodule
