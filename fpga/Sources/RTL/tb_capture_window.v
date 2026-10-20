`timescale 1ns/1ps
// Calls pure RTL functions, no forcing/depositing DUT registers or clocks.
module tb_capture_window;
    reg clk=0;always #10 clk=~clk;
    mvp5_capture dut(.clk(clk),.rst_n(1'b0),.raw_inputs(8'd0),
        .command_valid(1'b0),.command(8'd0),.argument_length(16'd0),
        .arguments(800'd0),.body_byte_index(11'd0),.stream_index(11'd0));
    capture_buffer buffer_dut(.clk(clk),.rst_n(1'b0),.sample_data(8'd0),.logic_tick(48'd0),
        .arm(1'b0),.stop(1'b0),.release_capture(1'b0),.sample_divisor(16'd1),
        .pre_count(32'd32767),.post_count(32'd32768),.trigger(1'b0),
        .read_enable(1'b0),.read_address(16'd0));
    capture_buffer_reference reference(.clk(clk),.rst_n(1'b0),.sample_data(8'd0),.logic_tick(48'd0),
        .arm(1'b0),.stop(1'b0),.release_capture(1'b0),.sample_divisor(16'd1),
        .pre_count(32'd32767),.post_count(32'd32768),.trigger(1'b0),
        .read_enable(1'b0),.read_address(16'd0));
    integer i,d,k,checks=0;
    reg [31:0] rng=32'h14ca701d;
    reg [15:0] count,divisor;
    reg [47:0] first,observed,last48,difference;
    reg [63:0] golden_span;
    reg [48:0] last49;
    reg expected,actual;
    task check;
        begin
            golden_span={48'd0,count}*divisor;
            if(dut.span(count,divisor)!==golden_span[47:0] || buffer_dut.span(count,divisor)!==golden_span[47:0])
                $fatal(1,"SPAN_BOUND count=%0d divisor=%0d",count,divisor);
            if(reference.span({16'd0,count},divisor)!==golden_span[47:0])
                $fatal(1,"REFERENCE_SPAN_MISMATCH");
            difference=observed-first; // Production keeps the original modulo subtraction.
            expected=difference<=reference.span({16'd0,count},divisor);
            actual=difference<=dut.span(count,divisor);
            if(actual !== expected) $fatal(1,"NARROW_WINDOW_RANGE_MISMATCH");
            checks=checks+1;
        end
    endtask
    initial begin
        for(d=0;d<4;d=d+1) begin
            divisor=d==0?1:d==1?5:d==2?10:50;
            for(i=0;i<4;i=i+1) begin
                count=i==0?0:i==1?1:i==2?32767:65535;
                first=i[0]?48'hfffffffffff0:48'd17;
                golden_span={48'd0,count}*divisor;last48=first+golden_span;
                observed=first;check();observed=first-1'b1;check();
                observed=last48;check();observed=last48+1'b1;check();
            end
        end
        for(i=0;i<32000;i=i+1) begin
            rng={rng[30:0],rng[31]^rng[21]^rng[1]^rng[0]};count=rng[15:0];
            divisor=i[1:0]==0?1:i[1:0]==1?5:i[1:0]==2?10:50;
            first=i[2]?48'hffffffffffff-{16'd0,rng}:{rng[15:0],rng};
            golden_span={48'd0,count}*divisor;last48=first+golden_span;
            case(i[5:3])
                0:observed=first;1:observed=first-1'b1;2:observed=last48;
                3:observed=last48+1'b1;4:observed=first+(golden_span>>1);
                default:observed={rng[31:16],rng};
            endcase
            check();
        end
        if(checks!=32064) $fatal(1,"WINDOW_CHECK_COUNT");
        $display("CAPTURE_SPAN_ARITHMETIC_PASS checks=%0d",checks);$finish;
    end
    initial begin #1000000;$fatal(1,"CAPTURE_WINDOW_GLOBAL_TIMEOUT");end
endmodule
