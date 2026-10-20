`timescale 1ns/1ps

// Independent schedule oracle: derive sample ticks from arm time/divisor,
// not DUT pointers, enables or state. Check every retained byte and metadata.
module tb_capture_buffer;
    localparam BITS=8, DEPTH=1<<BITS;
    reg clk=0; always #10 clk=~clk;
    reg rst_n=0, arm=0, stop=0, release_capture=0, trigger=0, read_enable=0;
    reg [47:0] tick=0;
    reg [15:0] divisor=1;
    reg [31:0] pre=128, post=127;
    reg [BITS-1:0] address=0;
    wire [2:0] state;
    wire ready, command_error, read_valid, trigger_valid, manual_stop;
    wire [7:0] data;
    wire [31:0] count, trigger_index;
    wire [47:0] first_tick, anchor_tick, decision_tick;
    integer cases=0, checked=0, d, p, k, seed=20261008;
    function [7:0] pattern;
        input [47:0] t;
        begin pattern=t[7:0]^t[15:8]^t[23:16]^t[31:24]^t[39:32]^t[47:40]^8'ha7; end
    endfunction
    capture_buffer #(.ADDR_BITS(BITS)) dut (
        .clk(clk),.rst_n(rst_n),.sample_data(pattern(tick)),.logic_tick(tick),
        .arm(arm),.stop(stop),.release_capture(release_capture),
        .sample_divisor(divisor),.pre_count(pre),.post_count(post),.trigger(trigger),
        .state(state),.ready(ready),.command_error(command_error),.valid_count(count),
        .trigger_valid(trigger_valid),.manual_stop(manual_stop),.first_tick(first_tick),
        .anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(read_enable),.read_address(address),.read_data(data),.read_valid(read_valid));

    task run_case;
        input integer div, before, trigger_cycle, stop_cycle;
        input [47:0] base;
        integer i, anchor_cycle, finish_cycle, last_sample, n, first_sample, j;
        reg [47:0] expected_tick;
        begin
            divisor=div; pre=before; post=DEPTH-1-before;
            anchor_cycle=trigger_cycle < 0 ? -1 : ((trigger_cycle+div-1)/div)*div;
            finish_cycle=stop_cycle >= 0 ? stop_cycle : anchor_cycle+post*div;
            if (finish_cycle < 1) $fatal(1,"Bad test schedule");
            for(i=0;i<=finish_cycle;i=i+1) begin
                @(negedge clk); tick=base+i; arm=i==0; stop=i==stop_cycle;
                trigger=i==trigger_cycle || (i==1 && before>1); // Early ignored trigger.
                read_enable=1; address=0;
                @(posedge clk); #1;
                if (i < finish_cycle && state == 4) $fatal(1,"Premature freeze case %0d",cases);
                if (i < finish_cycle && read_valid) $fatal(1,"Read exposed active RAM");
                if (i == before*div && before>0 && !ready) $fatal(1,"Prefill READY boundary");
            end
            @(negedge clk); arm=0; stop=0; trigger=0; read_enable=0;
            if (state != 4) $fatal(1,"Expected FROZEN case %0d",cases);
            last_sample=stop_cycle >= 0 ? ((finish_cycle-1)/div)*div : finish_cycle;
            n=last_sample/div;
            if (anchor_cycle >= 0 && anchor_cycle < finish_cycle)
                n=before+1+(last_sample-anchor_cycle)/div;
            else if(n>DEPTH) n=DEPTH;
            first_sample=n==0 ? 0 : last_sample-(n-1)*div;
            expected_tick=n==0 ? 0 : base+first_sample;
            if(count != n || first_tick !== expected_tick || manual_stop !== (stop_cycle>=0))
                $fatal(1,"Metadata case=%0d count=%0d/%0d first=%h/%h",cases,count,n,first_tick,expected_tick);
            if(trigger_valid !== (anchor_cycle>=0 && anchor_cycle<finish_cycle))
                $fatal(1,"Trigger validity case=%0d",cases);
            if(trigger_valid) begin
                expected_tick=base+anchor_cycle;
                if(anchor_tick !== expected_tick || trigger_index != before) $fatal(1,"Anchor/index");
                expected_tick=base+trigger_cycle;
                if(decision_tick !== expected_tick) $fatal(1,"Decision tick");
            end else if(anchor_tick != 0 || decision_tick != 0 || trigger_index != 32'hffffffff)
                $fatal(1,"Invalid trigger metadata not cleared");
            // Read all samples in reverse order, then repeat first: no hidden cursor.
            for(j=n-1;j>=0;j=j-1) begin
                @(negedge clk); read_enable=1; address=j;
                @(posedge clk); #1; expected_tick=base+first_sample+j*div;
                if(!read_valid || data !== pattern(expected_tick))
                    $fatal(1,"RAM mismatch case=%0d index=%0d got=%h expected=%h",cases,j,data,pattern(expected_tick));
                checked=checked+1;
            end
            @(negedge clk); read_enable=1; address=0; arm=1;
            @(posedge clk); #1;
            if(!command_error || state != 4 || count != n) $fatal(1,"ARM destroyed frozen evidence");
            if(n>0) begin
                expected_tick=base+first_sample;
                if(!read_valid || data !== pattern(expected_tick)) $fatal(1,"Repeat read changed data");
            end else if(read_valid) $fatal(1,"Empty capture exposed uninitialized RAM");
            @(negedge clk); arm=0; read_enable=0; release_capture=1;
            @(posedge clk); #1;
            if(state != 0 || count != 0 || trigger_valid) $fatal(1,"Release failed");
            @(negedge clk); release_capture=0;
            cases=cases+1;
        end
    endtask
    initial begin
        #100; @(negedge clk); rst_n=1;
        // All clock-enable phases, ring wrap, all divisor choices.
        for(k=0;k<4;k=k+1) begin
            case(k) 0:d=1; 1:d=5; 2:d=10; 3:d=50; endcase
            for(p=0;p<d;p=p+1) run_case(d,177, (DEPTH*2+177)*d+p+1,-1,48'hffffffff0000);
            run_case(d,0,DEPTH*d+1,-1,48'd12345);
            run_case(d,DEPTH-2,DEPTH*3*d+1,-1,48'd20000);
            run_case(d,128,-1,17*d+1,48'd0); // Partial PREFILL.
            run_case(d,128,-1,1,48'd0); // Empty stop.
            run_case(d,128,-1,(DEPTH+31)*d+1,48'd90000); // Manual ring wrap.
            run_case(d,128,DEPTH*d+1,(DEPTH+19)*d+1,48'd100000); // Partial post-window.
        end
        run_case(5,128,1001,1001,48'd0); // DISARM wins over trigger.
        for(k=0;k<64;k=k+1) begin
            d=($random(seed)&32'h7fffffff)%4;
            case(d) 0:d=1;1:d=5;2:d=10;3:d=50;endcase
            p=($random(seed)&32'h7fffffff)%(DEPTH-1);
            run_case(d,p,(DEPTH*2+p)*d+1,-1,48'hffffffffff00+k);
        end
        @(negedge clk); divisor=2; arm=1;
        @(posedge clk); #1; if(!command_error || state != 0) $fatal(1,"Bad divisor accepted");
        @(negedge clk); divisor=1; pre=32'hffffffff; post=0;
        @(posedge clk); #1; if(!command_error || state != 0) $fatal(1,"Bad geometry accepted");
        @(negedge clk); pre=DEPTH-1; post=0;
        @(posedge clk); #1; if(!command_error || state != 0) $fatal(1,"Zero post accepted");
        @(negedge clk); arm=0; rst_n=0;
        @(posedge clk); #1; if(state != 0 || read_valid) $fatal(1,"Reset did not clear metadata");
        $display("CAPTURE_BUFFER_TB_PASS cases=%0d samples=%0d phases=ALL wrap48=PASS partial=PASS replay=PASS",cases,checked);
        $finish;
    end
    initial begin #200_000_000; $fatal(1,"Capture bench watchdog"); end
endmodule
