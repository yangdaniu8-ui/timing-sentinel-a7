`timescale 1ns/1ps
// Exercise the actual top-level arbiter/FIFO/protocol/backend together.
// Inject decoded bytes, not fabricated board measurements. UART PHY has its
// separate unchanged regression. Replies are checked for CRC and byte identity.
module tb_button_demo_top;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,start_n=1,release_n=1;
    reg injected_valid=0;reg [7:0] injected_data=0;
    wire tx,ready,beep;wire [3:0] led;
    mvp5_top dut(.sys_clk(clk),.sys_rst_n(rst_n),.uart_rxd(1'b1),.uart_txd(tx),
        .led(led),.probe_inputs(8'b00010001),.target_ready(ready),.beep(beep),
        .mute_key_n(1'b1),.start_key_n(start_n),.release_key_n(release_n));
    defparam dut.controls.DEBOUNCE_CYCLES=4;
    defparam dut.controls.RELEASE_CYCLES=16;
    reg [7:0] received[0:2047],prior_reply[0:2047];integer count=0,checks=0,arms=0;
    integer i,j,n,base,first_end,waited;
    reg [15:0] crc;reg [3295:0] held_snapshot;
    function [15:0] crc_byte;
        input [15:0] c0;input [7:0] b;reg [15:0] c;integer k;
        begin c=c0^{b,8'd0};for(k=0;k<8;k=k+1)c=c[15]?(c<<1)^16'h1021:c<<1;crc_byte=c;end
    endfunction
    always @(posedge clk) begin
        if(dut.tx_valid && dut.tx_ready) begin received[count]=dut.tx_data;count=count+1;end
        if(dut.capture.arm) arms=arms+1;
        if(dut.local_busy && (dut.consume || dut.command_valid || !dut.link_idle))
            $fatal(1,"unsafe local ownership / host dispatch overlap");
    end
    task cycles;input integer c;begin repeat(c) @(negedge clk);end endtask
    task check;input ok;input [511:0] name;begin checks=checks+1;if(!ok)$fatal(1,"TOP_CHECK %0s",name);end endtask
    task push;input [7:0] b;begin
        @(negedge clk);injected_data=b;injected_valid=1;
        @(negedge clk);injected_valid=0;
    end endtask
    task request;input [7:0] cmd;input [31:0] id;input integer arg_len;input [31:0] arg;
        reg [7:0] b;integer k;reg [15:0] c;
        begin
            c=16'hffff;push(8'h54);push(8'h53);
            for(k=0;k<8;k=k+1) begin
                case(k)
                    0:b=1;1:b=cmd;2,3,4,5:b=id[(k-2)*8+:8];6:b=8+arg_len;7:b=0;
                endcase
                push(b);c=crc_byte(c,b);
            end
            for(k=0;k<8;k=k+1)begin b=k+1;push(b);c=crc_byte(c,b);end
            for(k=0;k<arg_len;k=k+1)begin b=arg[k*8+:8];push(b);c=crc_byte(c,b);end
            push(c[7:0]);push(c[15:8]);
        end
    endtask
    task reply;
        input integer from;input [15:0] status;
        integer size,k,timeout;reg [15:0] c;
        begin
            timeout=0;
            while((!dut.link_idle || !dut.tx_ready || dut.queue_valid || count==from) && timeout<2000000)begin cycles(1);timeout=timeout+1;end
            if(timeout>=2000000)$fatal(1,"reply timeout");
            size=12+{received[from+9],received[from+8]};
            check(count==from+size && received[from]==8'h54 && received[from+1]==8'h53,"reply framing");
            check({received[from+19],received[from+18]}==status,"backend status");
            c=16'hffff;for(k=from+2;k<from+size-2;k=k+1)c=crc_byte(c,received[k]);
            check(c=={received[from+size-1],received[from+size-2]},"reply CRC");
        end
    endtask
    initial begin
        force dut.rx_data=injected_data;force dut.rx_valid=injected_valid;
        cycles(6);rst_n=1;cycles(8);
        start_n=0;
        waited=0;while(!dut.local_busy && waited<30)begin cycles(1);waited=waited+1;end
        check(dut.local_busy,"start reservation");
        // A host SYNC starts while local WRITE is still in its division phase.
        request(3,0,0,0);start_n=1;reply(0,0);
        $display("BUTTON_ACTUAL session=%0d config=%0d arms=%0d profile=%b state=%0d status=%0d active=%0200h",
            dut.session_id,dut.config_id,arms,dut.panel_profile_ok,dut.capture_state,dut.command_status,dut.capture.active_config);
        check(dut.session_id==1 && dut.config_id==2 && arms==1 && dut.panel_profile_ok,"actual canonical config and ARM accepted");
        check(dut.synced && !dut.error_latched,"queued request preserved without overflow");
        // INFO reply is deliberately serialized at real UART speed. Pressing
        // START/RELEASE while that backend snapshot is in use must do nothing.
        base=count;request(2,1,0,0);
        waited=0;while(dut.link.state!=11 && waited<2000)begin cycles(1);waited=waited+1;end
        check(dut.link.state==11,"reply in flight");held_snapshot=dut.snapshot;
        start_n=0;release_n=0;cycles(40);start_n=1;release_n=1;
        check(!dut.local_busy && dut.snapshot===held_snapshot,"in-flight snapshot protected");reply(base,0);
        // End this button-owned acquisition using a normal host DISARM.
        base=count;request(8'h21,2,4,1);reply(base,0);
        check(dut.capture_state==4 && dut.session_id==1,"frozen retained");
        start_n=0;cycles(30);start_n=1;cycles(15);check(arms==1 && dut.capture_state==4,"frozen not overwritten");
        release_n=0;cycles(60);release_n=1;cycles(15);
        check(dut.capture_state==0 && !dut.local_busy,"owned release via actual backend");
        // Host ARM creates a different owner; physical release may not discard.
        base=count;request(8'h20,3,4,2);reply(base,0);
        base=count;request(8'h21,4,4,2);reply(base,0);
        release_n=0;cycles(60);release_n=1;cycles(10);
        check(dut.capture_state==4 && dut.session_id==2 && arms==2,"host-owned record preserved");
        // Duplicate read request is byte-identical and performs no new command.
        base=count;request(2,5,0,0);reply(base,0);first_end=count;
        for(i=base;i<first_end;i=i+1)prior_reply[i-base]=received[i];
        base=count;request(2,5,0,0);reply(base,0);
        // Explicit size, then per-byte check; no backend snapshot side effect.
        check(count-base==56,"INFO size");
        for(i=0;i<count-base;i=i+1)if(received[base+i]!==prior_reply[i])$fatal(1,"cache replay mismatch");
        check(dut.capture_state==4 && arms==2 && !dut.error_latched,"final state no unintended transaction");
        $display("BUTTON_DEMO_TOP_TB_PASS checks=%0d bytes=%0d ARM=%0d",checks,count,arms);$finish;
    end
    initial begin #150000000;$fatal(1,"TOP_GLOBAL_TIMEOUT");end
endmodule
