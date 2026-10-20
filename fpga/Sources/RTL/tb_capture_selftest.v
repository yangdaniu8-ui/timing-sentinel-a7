`timescale 1ns/1ps
module tb_capture_selftest;
    localparam BITS=10, DEPTH=1<<BITS;
    reg clk=0; always #10 clk=~clk;
    reg rst_n=0;
    reg [BITS-1:0] address=0;
    wire [7:0] data;
    wire [511:0] status;
    wire ready,passed,failed;
    integer i;
    reg [47:0] tick;
    function [7:0] pattern;
        input [47:0] t;
        begin pattern=t[7:0]^t[15:8]^t[23:16]^t[31:24]^t[39:32]^t[47:40]^8'ha7; end
    endfunction
    capture_selftest #(.ADDR_BITS(BITS)) dut(.clk(clk),.rst_n(rst_n),
        .diagnostic_address(address),.diagnostic_data(data),.diagnostic_ready(ready),
        .diagnostic_status(status),.passed(passed),.failed(failed));
    initial begin
        #100; @(negedge clk); rst_n=1;
        wait(ready); @(negedge clk);
        if(!passed || failed || status[31:24]!=9 || status[95:64]!=8*DEPTH+17)
            $fatal(1,"Self-test failed status=%h",status);
        for(i=0;i<DEPTH;i=i+1) begin
            @(negedge clk); address=i;
            @(posedge clk); #1;
            tick=status[335:288]+i*5;
            if(data !== pattern(tick)) $fatal(1,"Diagnostic BRAM port mismatch index=%0d",i);
        end
        $display("CAPTURE_SELFTEST_TB_PASS cases=9 samples=%0d final_read=%0d",status[95:64],DEPTH);
        $finish;
    end
    initial begin #50_000_000; $fatal(1,"Selftest watchdog"); end
endmodule
