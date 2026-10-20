`timescale 1ns/1ps

// Repeatable, explicitly INTERNAL UART/GPIO stimulus. Profile0 remains honest:
// this is a diagnostic harness, NOT the complete configurable MVP-1 device.
(* use_dsp="no" *) module rule_bringup_selftest #(
    parameter integer ADDR_BITS=16, parameter integer DEPTH=1<<ADDR_BITS,
    parameter integer NORMAL_DELAY=50000, parameter integer EARLY_DELAY=10000,
    parameter integer LATE_DELAY=110000, parameter integer EARLY_MIN=25000,
    parameter integer MAX_DELAY=100000, parameter integer BIT_PERIOD=434
) (
    input wire clk, input wire rst_n, input wire diagnostic_start,
    input wire [1:0] diagnostic_case,
    input wire [ADDR_BITS-1:0] diagnostic_address, output wire [7:0] diagnostic_data,
    input wire [13:0] event_byte_address, output wire [7:0] event_byte_data,
    output wire [31:0] event_byte_count, output reg [31:0] generation,
    output wire [511:0] diagnostic_status, output wire diagnostic_ready,
    output wire passed, output wire failed
);
    localparam ARM=0, PREFILL=1, SEND_UART=2, RUN=3, FINISH=4, QUALIFY=5;
    reg [2:0] state;
    reg [1:0] case_id;
    reg [47:0] tick;
    reg gpio_level, stimulus_has_a;
    reg [47:0] stimulus_a;
    reg [31:0] cycles;
    reg [3:0] final_result;
    reg [47:0] first_a, first_b, observed_b;
    reg [7:0] accepted_count, overlap_count;
    reg error_latched;
    wire arm=state==ARM;
    wire [31:0] pre=(DEPTH*7)/10;
    wire [31:0] post=DEPTH-1-pre;
    wire [47:0] min_delay=case_id==1 ? EARLY_MIN : 0;
    wire [47:0] max_delay=MAX_DELAY;
    wire [47:0] response_delay=case_id==0 ? NORMAL_DELAY :
        case_id==1 ? EARLY_DELAY : LATE_DELAY;
    wire stimulus_serial, stimulus_ready;
    uart_tx #(.CLOCK_HZ(BIT_PERIOD*115200)) stimulus (
        .clk(clk),.rst_n(rst_n),.data(8'ha5),.valid(state==SEND_UART),
        .ready(stimulus_ready),.serial_out(stimulus_serial));
    wire [7:0] raw={6'd0,gpio_level,stimulus_serial};
    wire [7:0] levels,rising,falling;
    input_frontend frontend(.clk(clk),.rst_n(rst_n),.clear_history(arm),
        .raw(raw),.levels(levels),.rising(rising),.falling(falling));
    wire uart_valid,uart_error;
    wire [7:0] uart_byte;
    target_uart_monitor monitor(.clk(clk),.rst_n(rst_n),.clear(state==ARM || state==PREFILL),
        .serial_level(levels[0]),.bit_period(BIT_PERIOD),
        .byte_valid(uart_valid),.frame_error(uart_error),.data(uart_byte));
    wire [2:0] capture_state;
    wire ready,command_error,trigger_valid,manual_stop,read_valid;
    wire [31:0] count,trigger_index;
    wire [47:0] first_tick,anchor_tick,decision_tick;
    wire busy,accepted,overlap,result_valid,matched_b;
    wire [3:0] result;
    wire [47:0] result_a,result_b;
    wire timeout_stop=state==RUN && cycles>DEPTH*5+MAX_DELAY+BIT_PERIOD*20+1024;
    uart_gpio_rule rule0(.clk(clk),.rst_n(rst_n),.enable(ready),.cancel(arm || timeout_stop),
        .logic_tick(tick),.uart_valid(uart_valid),.uart_data(uart_byte),
        .byte_value(8'ha5),.byte_mask(8'hff),.gpio_edge(rising[1]),
        .min_ticks(min_delay),.max_ticks(max_delay),.busy(busy),
        .accepted(accepted),.overlap(overlap),.result_valid(result_valid),.result(result),
        .result_a(result_a),.result_b(result_b),.matched_b(matched_b));
    capture_buffer #(.ADDR_BITS(ADDR_BITS)) samples (
        .clk(clk),.rst_n(rst_n),.sample_data(levels),.logic_tick(tick),
        .arm(arm),.stop(timeout_stop),.release_capture(state==FINISH && diagnostic_start),
        .sample_divisor(16'd5),.pre_count(pre),.post_count(post),.trigger(result_valid),
        .state(capture_state),.ready(ready),.command_error(command_error),.valid_count(count),
        .trigger_valid(trigger_valid),.manual_stop(manual_stop),.first_tick(first_tick),
        .anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(diagnostic_ready),.read_address(diagnostic_address),
        .read_data(diagnostic_data),.read_valid(read_valid));
    wire [15:0] event_count;
    wire [31:0] event_overwrites;
    wire [127:0] event_read_data;
    wire event_read_valid;
    wire active=capture_state>=1 && capture_state<=3;
    // Filter UART and the response GPIO; no high-rate auxiliary toggles logged.
    wire event_enable=active && (uart_valid || uart_error || rising[1] || falling[1] || result_valid || overlap);
    wire [127:0] event_record={16'd0,{3'd0,capture_state},
        {3'd0,accepted},{3'd0,overlap},{12'd0,result},uart_error,uart_byte,
        uart_valid,falling,rising,levels,tick};
    event_buffer events(.clk(clk),.rst_n(rst_n),.clear(arm),
        .write_enable(event_enable),.event_data(event_record),.count(event_count),
        .overwrites(event_overwrites),.frozen(capture_state==4),
        .read_enable(diagnostic_ready),.read_address(event_byte_address[13:4]),
        .read_data(event_read_data),.read_valid(event_read_valid));
    assign event_byte_data=event_read_valid ? event_read_data >> (event_byte_address[3:0]*8) : 0;
    assign event_byte_count={16'd0,event_count}<<4;
    assign diagnostic_ready=state==FINISH;
    wire [3:0] expected_result=case_id==0 ? 1 : case_id==1 ? 2 : 3;
    assign passed=diagnostic_ready && !error_latched && final_result==expected_result &&
        accepted_count==1 && overlap_count==0 && event_overwrites==0 &&
        trigger_valid && !manual_stop && count==DEPTH;
    assign failed=diagnostic_ready && !passed;
    // 64-byte LE schema2: schema,state,case,result,generation,count,div,event_count,
    // first/anchor/decision/A/B/observedB (six 48-bit ticks), index,flags,
    // accepted,overlap,error,event_overwrites. All unused B values are zero.
    assign diagnostic_status={event_overwrites,{7'd0,error_latched},overlap_count,accepted_count,
        {3'd0,manual_stop,observed_b!=0,(final_result==1 || final_result==2),first_a!=0,trigger_valid},
        trigger_index,observed_b,first_b,first_a,decision_tick,anchor_tick,first_tick,
        event_count,16'd5,count,generation,{4'd0,final_result},{6'd0,case_id},
        (diagnostic_ready ? (passed ? 8'd1 : 8'd2) : 8'd0),8'd2};
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=ARM; case_id<=0; generation<=1; tick<=48'd1000;
            gpio_level<=0; stimulus_has_a<=0; stimulus_a<=0; cycles<=0;
            final_result<=0; first_a<=0; first_b<=0; observed_b<=0;
            accepted_count<=0; overlap_count<=0; error_latched<=0;
        end else begin
            tick<=tick+1'b1;
            if(command_error || uart_error) error_latched<=1;
            if(state==ARM) begin
                gpio_level<=0; stimulus_has_a<=0; cycles<=0;
                final_result<=0; first_a<=0; first_b<=0; observed_b<=0;
                accepted_count<=0; overlap_count<=0; error_latched<=0; state<=PREFILL;
            end else if(state==PREFILL && ready) begin state<=QUALIFY; cycles<=0; end
            else if(state==QUALIFY) begin
                cycles<=cycles+1'b1;
                if(cycles==BIT_PERIOD+4) state<=SEND_UART;
            end
            else if(state==SEND_UART && stimulus_ready) state<=RUN;
            else if(state==RUN) begin
                cycles<=cycles+1'b1;
                if(uart_valid) begin stimulus_has_a<=1; stimulus_a<=tick; end
                // Three edges from a raw registered transition to the shared
                // frontend observation edge; independent decoder defines A.
                if(stimulus_has_a && case_id!=3) begin
                    if(tick-stimulus_a==response_delay-3) gpio_level<=1;
                    if(tick-stimulus_a==response_delay+BIT_PERIOD*2-3) gpio_level<=0;
                end
                if(accepted) begin accepted_count<=accepted_count+1'b1; first_a<=tick; end
                if(overlap) overlap_count<=overlap_count+1'b1;
                if(rising[1] && first_a!=0 && observed_b==0) observed_b<=tick;
                if(result_valid && final_result==0) begin
                    final_result<=result; first_a<=result_a; first_b<=result_b;
                end
                if(timeout_stop) error_latched<=1;
                if(capture_state==4) state<=FINISH;
            end else if(state==FINISH && diagnostic_start) begin
                case_id<=diagnostic_case; generation<=generation+1'b1; state<=ARM;
            end
        end
    end
endmodule
