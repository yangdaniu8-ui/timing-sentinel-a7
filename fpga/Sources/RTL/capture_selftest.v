`timescale 1ns/1ps

// Boot-time BRAM capture/read self-test; NOT a monitored external capture.
// Nine schedules, all retained bytes checked. Last internal record stays frozen.
(* use_dsp="no" *) module capture_selftest #(
    parameter integer ADDR_BITS=16,
    parameter integer DEPTH=1<<ADDR_BITS
) (
    input wire clk, input wire rst_n,
    input wire [ADDR_BITS-1:0] diagnostic_address,
    output wire [7:0] diagnostic_data,
    output wire diagnostic_ready, output wire [511:0] diagnostic_status,
    output wire passed, output wire failed
);
    localparam START=0, CAPTURE=1, CHECK_META=2, READ=3, CHECK=4, RELEASE=5, FINISH=6;
    reg [2:0] test_state;
    // case_number is four bits: case 8 verifies 48-bit timestamp wrap.
    reg [3:0] case_id;
    reg [31:0] cycle, index, errors, checked_samples;
    reg [31:0] case_errors, first_fail_index;
    reg [7:0] passed_cases, first_fail_case, first_expected, first_actual;
    reg [15:0] div;
    reg [31:0] pre, post, trigger_cycle, stop_cycle, expected_count;
    reg [47:0] base, expected_first, expected_anchor, expected_decision;
    wire [47:0] tick=base+cycle;
    wire [2:0] capture_state;
    wire ready, command_error, read_valid, trigger_valid, manual_stop;
    wire [7:0] data;
    wire [31:0] count, trigger_index;
    wire [47:0] first_tick, anchor_tick, decision_tick;
    wire manual_case=case_id==6 || case_id==7;
    wire arm=test_state==START;
    wire trigger=test_state==CAPTURE && !manual_case && cycle==trigger_cycle;
    wire stop=test_state==CAPTURE && manual_case && cycle==stop_cycle;
    wire release_capture=test_state==RELEASE;
    wire read_enable=test_state==READ || test_state==FINISH;
    wire [ADDR_BITS-1:0] address=test_state==FINISH ? diagnostic_address : index[ADDR_BITS-1:0];
    function [47:0] span;
        input [31:0] count;
        input [15:0] divisor;
        reg [47:0] n;
        begin
            n={16'd0,count};
            case(divisor) 1:span=n;5:span=(n<<2)+n;
                10:span=(n<<3)+(n<<1);50:span=(n<<5)+(n<<4)+(n<<1);
                default:span=0;endcase
        end
    endfunction
    wire [47:0] expected_sample_tick=expected_first+span(index,div);
    function [7:0] pattern;
        input [47:0] t;
        begin pattern=t[7:0]^t[15:8]^t[23:16]^t[31:24]^t[39:32]^t[47:40]^8'ha7; end
    endfunction
    wire bad_sample=!read_valid || data!=pattern(expected_sample_tick);
    wire bad_meta=count!=expected_count || first_tick!=expected_first ||
        trigger_valid!=!manual_case || manual_stop!=manual_case ||
        (manual_case ? (anchor_tick!=0 || decision_tick!=0 || trigger_index!=32'hffffffff) :
        (anchor_tick!=expected_anchor || decision_tick!=expected_decision || trigger_index!=pre));
    assign diagnostic_ready=test_state==FINISH;
    assign diagnostic_data=data;
    assign passed=diagnostic_ready && errors==0 && passed_cases==9;
    assign failed=diagnostic_ready && !passed;
    // LE64-byte schema: see docs/capture_stage3.md. Snapshot at protocol dispatch.
    assign diagnostic_status={32'd1,8'd8,8'd1,trigger_index,decision_tick,anchor_tick,first_tick,
        post,pre,count,div,first_actual,first_expected,first_fail_index,24'd0,first_fail_case,
        checked_samples,errors,passed_cases,8'd9,(diagnostic_ready ? (passed ? 8'd1 : 8'd2) : 8'd0),8'd1};
    capture_buffer #(.ADDR_BITS(ADDR_BITS)) buffer (
        .clk(clk),.rst_n(rst_n),.sample_data(pattern(tick)),.logic_tick(tick),
        .arm(arm),.stop(stop),.release_capture(release_capture),
        .sample_divisor(div),.pre_count(pre),.post_count(post),.trigger(trigger),
        .state(capture_state),.ready(ready),.command_error(command_error),.valid_count(count),
        .trigger_valid(trigger_valid),.manual_stop(manual_stop),.first_tick(first_tick),
        .anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(read_enable),.read_address(address),.read_data(data),.read_valid(read_valid));

    // Combinational schedule independently derives expected geometry.
    always @* begin
        case(case_id) 0:div=1; 1:div=5; 2:div=10; 3:div=50;
            7:div=10; default:div=5; endcase
        pre=(DEPTH*7)/10;
        if(case_id==4) pre=0;
        if(case_id==5) pre=DEPTH-2;
        post=DEPTH-1-pre;
        base=case_id==8 ? 48'hffffffff0000 : 48'd1000;
        trigger_cycle=span(DEPTH*2+pre,div)+1;
        // Full cases: next enable after off-phase trigger (same tick for div1).
        expected_anchor=base+span(DEPTH*2+pre,div)+(div==1 ? 1 : div);
        expected_decision=base+trigger_cycle;
        expected_first=expected_anchor-span(pre,div);
        expected_count=DEPTH;
        stop_cycle=0;
        if(case_id==6) begin
            stop_cycle=span(17,div)+1; expected_count=17; expected_first=base+div;
        end
        if(case_id==7) begin
            stop_cycle=span(DEPTH+19,div)+1; expected_first=base+span(20,div);
        end
    end
    always @(posedge clk) begin
        if(!rst_n) begin
            test_state<=START; case_id<=0; cycle<=0; index<=0;
            errors<=0; checked_samples<=0; passed_cases<=0; case_errors<=0;
            first_fail_case<=8'hff; first_fail_index<=32'hffffffff;
            first_expected<=0; first_actual<=0;
        end else case(test_state)
            START: begin cycle<=1; case_errors<=errors; test_state<=CAPTURE; end
            CAPTURE: begin
                cycle<=cycle+1;
                if(capture_state==4) begin index<=0; test_state<=CHECK_META; end
                // Bounded test schedule; hardware must not hang forever.
                else if(cycle>span(DEPTH*4+64,div)) begin
                    errors<=errors+1; first_fail_case<=case_id;
                    first_fail_index<=32'hfffffffe; test_state<=FINISH;
                end
            end
            CHECK_META: begin
                if(bad_meta) begin
                    errors<=errors+1;
                    if(errors==0) begin first_fail_case<=case_id;
                        first_fail_index<=32'hfffffffd; first_expected<=0; first_actual<=8'hff; end
                end
                test_state<=READ;
            end
            READ: test_state<=CHECK;
            CHECK: begin
                checked_samples<=checked_samples+1;
                if(bad_sample) begin
                    errors<=errors+1;
                    if(errors==0) begin first_fail_case<=case_id; first_fail_index<=index;
                        first_expected<=pattern(expected_sample_tick); first_actual<=data; end
                end
                if(index==expected_count-1) begin
                    if(errors==case_errors && !bad_sample) passed_cases<=passed_cases+1;
                    test_state<=case_id==8 ? FINISH : RELEASE;
                end else begin index<=index+1; test_state<=READ; end
            end
            RELEASE: begin case_id<=case_id+1; cycle<=0; test_state<=START; end
            FINISH: test_state<=FINISH;
            default: test_state<=FINISH;
        endcase
    end
endmodule
