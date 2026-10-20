`timescale 1ns/1ps
// Stage4b bridge: REAL external pins, fixed rule. Still profile0 diagnostic,
// not the complete configurable MVP-1. No internal stimulus or auto ARM.
(* use_dsp="no" *) module external_rule_capture #(
    parameter integer ADDR_BITS=16, parameter integer DEPTH=1<<ADDR_BITS,
    parameter integer BIT_PERIOD=434, parameter integer MIN_DELAY=25000,
    parameter integer MAX_DELAY=100000, parameter integer WATCHDOG_CYCLES=25000000
) (
    input wire clk, input wire rst_n, input wire diagnostic_start, input wire diagnostic_stop,
    input wire target_uart, input wire target_gpio, output wire target_ready,
    input wire [ADDR_BITS-1:0] diagnostic_address, output wire [7:0] diagnostic_data,
    input wire [13:0] event_byte_address, output wire [7:0] event_byte_data,
    output wire [31:0] event_byte_count, output reg [31:0] generation,
    output wire [511:0] diagnostic_status, output wire diagnostic_ready,
    output wire frozen, output wire failed
);
    localparam IDLE=0, ARM=1, ACTIVE=2, FINISH=3;
    reg [1:0] state;
    reg [47:0] tick, first_a, first_b, observed_b;
    reg have_a, have_b, have_observed, error_latched;
    reg [3:0] final_result;
    reg [7:0] accepted_count, overlap_count;
    reg [31:0] elapsed;
    wire arm=state==ARM;
    wire [31:0] pre=(DEPTH*7)/10;
    wire [31:0] post=DEPTH-1-pre;
    wire [7:0] levels,rising,falling;
    input_frontend frontend(.clk(clk),.rst_n(rst_n),.clear_history(arm),
        .raw({6'd0,target_gpio,target_uart}),.levels(levels),.rising(rising),.falling(falling));
    wire [2:0] capture_state;
    wire ready,command_error,trigger_valid,manual_stop,read_valid;
    wire [31:0] count,trigger_index;
    wire [47:0] first_tick,anchor_tick,decision_tick;
    wire active=capture_state>=1 && capture_state<=3;
    wire watchdog=state==ACTIVE && elapsed>=WATCHDOG_CYCLES-1;
    wire stop=(diagnostic_stop || watchdog) && active;
    wire uart_valid,uart_error;
    wire [7:0] uart_byte;
    target_uart_monitor monitor(.clk(clk),.rst_n(rst_n),.clear(arm || capture_state==1 || !active),
        .serial_level(levels[0]),.bit_period(BIT_PERIOD),
        .byte_valid(uart_valid),.frame_error(uart_error),.data(uart_byte));
    wire busy,accepted,overlap,result_valid,matched_b;
    wire [3:0] result;
    wire [47:0] result_a,result_b;
    wire [47:0] min_ticks=MIN_DELAY;
    wire [47:0] max_ticks=MAX_DELAY;
    uart_gpio_rule rule0(.clk(clk),.rst_n(rst_n),.enable(target_ready && !stop),.cancel(arm || stop),
        .logic_tick(tick),.uart_valid(uart_valid),.uart_data(uart_byte),
        .byte_value(8'ha5),.byte_mask(8'hff),.gpio_edge(rising[1]),
        .min_ticks(min_ticks),.max_ticks(max_ticks),.busy(busy),
        .accepted(accepted),.overlap(overlap),.result_valid(result_valid),.result(result),
        .result_a(result_a),.result_b(result_b),.matched_b(matched_b));
    capture_buffer #(.ADDR_BITS(ADDR_BITS)) samples (
        .clk(clk),.rst_n(rst_n),.sample_data(levels),.logic_tick(tick),
        .arm(arm),.stop(stop),.release_capture(state==FINISH && diagnostic_start),
        .sample_divisor(16'd5),.pre_count(pre),.post_count(post),.trigger(result_valid),
        .state(capture_state),.ready(ready),.command_error(command_error),.valid_count(count),
        .trigger_valid(trigger_valid),.manual_stop(manual_stop),.first_tick(first_tick),
        .anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(frozen),.read_address(diagnostic_address),
        .read_data(diagnostic_data),.read_valid(read_valid));
    wire [15:0] event_count;
    wire [31:0] event_overwrites;
    wire [127:0] event_read_data;
    wire event_read_valid;
    wire event_enable=active && !stop && (uart_valid || uart_error || rising[1] || falling[1] || result_valid || overlap);
    wire [127:0] event_record={16'd0,{3'd0,capture_state},
        {3'd0,accepted},{3'd0,overlap},{12'd0,result},uart_error,uart_byte,
        uart_valid,falling,rising,levels,tick};
    event_buffer events(.clk(clk),.rst_n(rst_n),.clear(arm),
        .write_enable(event_enable),.event_data(event_record),.count(event_count),
        .overwrites(event_overwrites),.frozen(capture_state==4),
        .read_enable(frozen),.read_address(event_byte_address[13:4]),
        .read_data(event_read_data),.read_valid(event_read_valid));
    assign event_byte_data=event_read_valid ? event_read_data >> (event_byte_address[3:0]*8) : 0;
    assign event_byte_count={16'd0,event_count}<<4;
    assign diagnostic_ready=state==IDLE || state==FINISH;
    assign frozen=state==FINISH;
    assign target_ready=ready && state==ACTIVE;
    assign failed=frozen && error_latched;
    // Observed B must be inside the actual saved sample time range. A manual
    // off-phase STOP can exclude a last event from the waveform; do not claim it.
    wire [47:0] last_tick=first_tick+((count-1)<<2)+(count-1);
    wire [47:0] observed_from_first=observed_b-first_tick;
    wire [47:0] span=last_tick-first_tick;
    wire observed_in_window=have_observed && count!=0 && observed_from_first<=span;
    // schema3 uses byte2=255 (actual external scene UNKNOWN), no desired-result
    // comparison. state byte: 0 idle, 3 active, 1 frozen, 2 frozen with errors.
    assign diagnostic_status={event_overwrites,{7'd0,error_latched},overlap_count,accepted_count,
        {3'd0,manual_stop,observed_in_window,have_b,have_a,trigger_valid},trigger_index,
        (observed_in_window ? observed_b : 48'd0),first_b,first_a,decision_tick,anchor_tick,first_tick,
        event_count,16'd5,count,generation,{4'd0,final_result},8'hff,
        (frozen ? (failed ? 8'd2 : 8'd1) : state==IDLE ? 8'd0 : 8'd3),8'd3};
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=IDLE;generation<=0;tick<=48'd1000;elapsed<=0;
            first_a<=0;first_b<=0;observed_b<=0;have_a<=0;have_b<=0;have_observed<=0;
            final_result<=0;accepted_count<=0;overlap_count<=0;error_latched<=0;
        end else begin
            tick<=tick+1'b1;
            if((state==IDLE || state==FINISH) && diagnostic_start) begin
                state<=ARM;generation<=generation+1'b1;
            end else if(state==ARM) begin
                state<=ACTIVE;elapsed<=0;first_a<=0;first_b<=0;observed_b<=0;
                have_a<=0;have_b<=0;have_observed<=0;final_result<=0;
                accepted_count<=0;overlap_count<=0;error_latched<=0;
            end else if(state==ACTIVE) begin
                if(elapsed!=32'hffffffff) elapsed<=elapsed+1'b1;
                if(command_error || uart_error) error_latched<=1;
                if(accepted) begin
                    have_a<=1;first_a<=tick;
                    if(accepted_count!=8'hff) accepted_count<=accepted_count+1'b1;
                end
                if(overlap && overlap_count!=8'hff) overlap_count<=overlap_count+1'b1;
                if(active && !stop && rising[1] && (have_a || accepted) && !have_observed) begin
                    have_observed<=1;observed_b<=tick;
                end
                if(result_valid && final_result==0) begin
                    final_result<=result;first_a<=result_a;first_b<=result_b;have_b<=matched_b;
                end
                if(capture_state==4) state<=FINISH;
            end
        end
    end
endmodule
