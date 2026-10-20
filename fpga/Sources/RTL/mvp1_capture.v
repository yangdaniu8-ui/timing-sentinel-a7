`timescale 1ns/1ps
// Configurable real-input single-rule profile1. No stimulus, watchdog or auto
// ARM. A new capture requires matching RELEASE of the preceding frozen session.
(* use_dsp="no" *) module mvp1_capture #(
    parameter integer CLOCK_HZ=50000000,
    parameter integer ADDR_BITS=16,parameter integer DEPTH=1<<ADDR_BITS,
    parameter [127:0] FIRMWARE_ID=128'h000000302e312e302d37612d3170766d
) (
    input wire clk,input wire rst_n,input wire [7:0] raw_inputs,output wire target_ready,
    input wire command_valid,input wire [7:0] command,input wire [15:0] argument_length,
    input wire [271:0] arguments,output reg command_done,output reg [15:0] command_status,
    output reg [10:0] body_length,output reg [1055:0] body_snapshot,
    output reg stream_response,input wire [10:0] stream_index,output wire [7:0] stream_data,
    output wire [2:0] state,output reg [31:0] session_id,output reg [31:0] config_id
);
    localparam [31:0] DEFAULT_PRE=(DEPTH==65536 ? 45874 : (DEPTH*7)/10);
    localparam [31:0] DEFAULT_POST=DEPTH-1-DEFAULT_PRE;
    localparam [271:0] DEFAULT_CONFIG={48'd100000,48'd0,8'd0,8'd2,8'hff,8'ha5,
        DEFAULT_POST,DEFAULT_PRE,16'd5,32'd115200,8'd0,8'd1,8'd0,8'd1};
    localparam [15:0] OK=0,UNKNOWN_COMMAND=2,BAD_LENGTH=3,INVALID_ARGUMENT=4,
        BUSY=5,STALE_SESSION=6,NOT_READY=8,INTERNAL_ERROR=12,CONFIG_MISMATCH=14;
    reg [271:0] active_config,shadow,captured_config;
    reg shadow_valid;
    reg [31:0] period,shadow_period;
    reg arm,stop,release_capture;
    reg [1:0] command_phase;
    reg [31:0] division_remainder,division_quotient,division_denominator;
    reg [5:0] division_left;
    wire [32:0] trial_remainder={division_remainder,division_quotient[31]};
    wire division_subtract=trial_remainder>={1'b0,division_denominator};
    wire [31:0] next_quotient={division_quotient[30:0],division_subtract};
    wire [31:0] cfg_baud=arguments[32+:32];
    wire [15:0] cfg_divisor=arguments[64+:16];
    wire [31:0] cfg_pre=arguments[80+:32],cfg_post=arguments[112+:32];
    wire [47:0] cfg_min=arguments[176+:48],cfg_max=arguments[224+:48];
    wire config_valid=arguments[7:0]==1 && arguments[15:8]<8 && arguments[23:16]<8 &&
        arguments[15:8]!=arguments[23:16] && arguments[31:24]<2 &&
        cfg_baud>=1200 && cfg_baud<=1000000 &&
        (cfg_divisor==1 || cfg_divisor==5 || cfg_divisor==10 || cfg_divisor==50) &&
        cfg_pre<DEPTH && cfg_post>=1 && cfg_post<DEPTH &&
        {1'b0,cfg_pre}+{1'b0,cfg_post}+1==DEPTH &&
        (arguments[151:144]&~arguments[159:152])==0 && arguments[167:160]<3 &&
        arguments[175:168]==0 && cfg_min<=cfg_max && !cfg_max[47];
    reg [47:0] tick,selected_a,selected_b,observed_b;
    reg have_a,have_b,have_observed,trigger_locked;
    reg [3:0] final_result;
    reg [31:0] accepted_count,pass_count,early_count,timeout_count,cancelled_count;
    wire [7:0] levels,rising,falling;
    wire [2:0] uart_channel=active_config[10:8],gpio_channel=active_config[18:16];
    wire gpio_edge=active_config[24] ? falling[gpio_channel] : rising[gpio_channel];
    wire [15:0] divisor=active_config[64+:16];
    wire [47:0] min_ticks=active_config[176+:48],max_ticks=active_config[224+:48];
    wire active=state>=1 && state<=3;
    wire [2:0] reported_state=trigger_locked && state==2 ? 3'd3 : state;
    wire ready,trigger_valid,manual_stop,read_valid,command_error;
    wire [31:0] sample_count,trigger_index;
    wire [47:0] first_tick,anchor_tick,decision_tick;
    wire uart_valid,uart_error;wire [7:0] uart_byte;
    wire busy,accepted,overlap,result_valid,matched_b;wire [3:0] result;
    wire [47:0] result_a,result_b;
    wire result_trigger=result_valid && (active_config[167:160]==2 || (active_config[167:160]==1 && result!=1));
    assign target_ready=ready && !trigger_locked && !stop;
    input_frontend frontend(.clk(clk),.rst_n(rst_n),.clear_history(arm || state==1),
        .raw(raw_inputs),.levels(levels),.rising(rising),.falling(falling));
    target_uart_monitor monitor(.clk(clk),.rst_n(rst_n),.clear(arm || state==1 || !active),
        .serial_level(levels[uart_channel]),.bit_period(period),
        .byte_valid(uart_valid),.frame_error(uart_error),.data(uart_byte));
    uart_gpio_rule rule0(.clk(clk),.rst_n(rst_n),.enable(target_ready),.cancel(arm || stop),
        .logic_tick(tick),.uart_valid(uart_valid),.uart_data(uart_byte),
        .byte_value(active_config[151:144]),.byte_mask(active_config[159:152]),.gpio_edge(gpio_edge),
        .min_ticks(min_ticks),.max_ticks(max_ticks),.busy(busy),.accepted(accepted),.overlap(overlap),
        .result_valid(result_valid),.result(result),.result_a(result_a),.result_b(result_b),.matched_b(matched_b));
    reg [31:0] read_offset;
    reg reading_events;
    wire [31:0] byte_address=read_offset+stream_index;
    wire [7:0] sample_data;
    capture_buffer #(.ADDR_BITS(ADDR_BITS)) samples(.clk(clk),.rst_n(rst_n),
        .sample_data(levels),.logic_tick(tick),.arm(arm),.stop(stop),.release_capture(release_capture),
        .sample_divisor(divisor),.pre_count(active_config[80+:32]),.post_count(active_config[112+:32]),
        .trigger(result_trigger),.state(state),.ready(ready),.command_error(command_error),
        .valid_count(sample_count),.trigger_valid(trigger_valid),.manual_stop(manual_stop),
        .first_tick(first_tick),.anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(state==4),.read_address(byte_address[ADDR_BITS-1:0]),.read_data(sample_data),.read_valid(read_valid));
    wire [15:0] event_count;wire [31:0] event_overwrites;wire [127:0] event_word;
    wire event_read_valid;
    wire event_enable=(state==2 || state==3) && !stop &&
        (uart_valid || uart_error || rising[gpio_channel] || falling[gpio_channel] || result_valid || overlap);
    wire [127:0] event_record={16'd0,{3'd0,state},{3'd0,accepted},{3'd0,overlap},
        {12'd0,result},uart_error,uart_byte,uart_valid,falling,rising,levels,tick};
    event_buffer events(.clk(clk),.rst_n(rst_n),.clear(arm),.write_enable(event_enable),
        .event_data(event_record),.count(event_count),.overwrites(event_overwrites),.frozen(state==4),
        .read_enable(state==4),.read_address(byte_address[13:4]),.read_data(event_word),.read_valid(event_read_valid));
    assign stream_data=reading_events ? (event_read_valid ? event_word>>(byte_address[3:0]*8) : 8'd0) : sample_data;
    function [47:0] sample_span;
        input [31:0] count;input [15:0] div;reg [47:0] n;
        begin n={16'd0,count};case(div)
            1:sample_span=n;5:sample_span=(n<<2)+n;10:sample_span=(n<<3)+(n<<1);
            50:sample_span=(n<<5)+(n<<4)+(n<<1);default:sample_span=0;endcase end
    endfunction
    wire observed_in_window=have_observed && sample_count!=0 &&
        (observed_b-first_tick)<=sample_span(sample_count-1,divisor);
    reg [1055:0] metadata;
    always @* begin
        metadata=0;
        metadata[0+:8]=1;
        metadata[8+:8]={3'd0,manual_stop,observed_in_window,have_b,have_a,trigger_valid};
        metadata[16+:32]=session_id;metadata[48+:32]=config_id;
        metadata[80+:32]=CLOCK_HZ;metadata[112+:32]=sample_count;metadata[144+:16]=divisor;
        metadata[160+:48]=first_tick;metadata[208+:48]=trigger_valid ? anchor_tick : 48'd0;
        metadata[256+:48]=trigger_valid ? decision_tick : 48'd0;metadata[304+:32]=trigger_index;
        metadata[336+:48]=have_a ? selected_a : 48'd0;metadata[384+:48]=have_b ? selected_b : 48'd0;
        metadata[432+:48]=have_a ? selected_a+max_ticks : 48'd0;
        metadata[480+:48]=observed_in_window ? observed_b : 48'd0;
        metadata[528+:8]={4'd0,final_result};metadata[536+:16]=event_count;
        metadata[552+:32]=event_overwrites;metadata[584+:32]=accepted_count;
        metadata[616+:32]=pass_count;metadata[648+:32]=early_count;metadata[680+:32]=timeout_count;
        metadata[712+:32]=cancelled_count;metadata[776+:8]=255;metadata[784+:272]=captured_config;
    end
    always @(posedge clk) begin
        if(!rst_n) begin
            tick<=1000;selected_a<=0;selected_b<=0;observed_b<=0;have_a<=0;have_b<=0;
            have_observed<=0;trigger_locked<=0;final_result<=0;captured_config<=DEFAULT_CONFIG;
            accepted_count<=0;pass_count<=0;early_count<=0;timeout_count<=0;cancelled_count<=0;
        end else begin
            tick<=tick+1'b1;
            if(arm) begin
                selected_a<=0;selected_b<=0;observed_b<=0;have_a<=0;have_b<=0;have_observed<=0;
                trigger_locked<=0;final_result<=0;captured_config<=active_config;
                accepted_count<=0;pass_count<=0;early_count<=0;timeout_count<=0;cancelled_count<=0;
            end else if(active) begin
                if(accepted) begin
                    if(accepted_count!=32'hffffffff) accepted_count<=accepted_count+1'b1;
                    if(!trigger_locked) begin
                        selected_a<=tick;have_a<=1;have_b<=0;selected_b<=0;
                        have_observed<=0;observed_b<=0;final_result<=0;
                    end
                end
                if(gpio_edge && !stop && (have_a || accepted) && (!have_observed || accepted)) begin
                    observed_b<=tick;have_observed<=1;
                end
                if(result_valid) begin
                    final_result<=result;selected_a<=result_a;selected_b<=result_b;have_a<=1;have_b<=matched_b;
                    if(result==1 && pass_count!=32'hffffffff) pass_count<=pass_count+1'b1;
                    if(result==2 && early_count!=32'hffffffff) early_count<=early_count+1'b1;
                    if(result==3 && timeout_count!=32'hffffffff) timeout_count<=timeout_count+1'b1;
                    if(result_trigger) trigger_locked<=1;
                end
                if(stop && busy) begin
                    final_result<=4;have_b<=0;selected_b<=0;
                    if(cancelled_count!=32'hffffffff) cancelled_count<=cancelled_count+1'b1;
                end
            end
        end
    end
    task reply_error;
        input [15:0] status;
        begin command_status<=status;body_length<=0;body_snapshot<=0;stream_response<=0;command_done<=1;end
    endtask
    wire [31:0] request_session=arguments[31:0],request_offset=arguments[63:32];
    wire [15:0] request_count=arguments[79:64];
    wire [31:0] read_limit=command==8'h30 ? {16'd0,event_count}<<4 : sample_count;
    wire [15:0] clipped_count=request_count>read_limit-request_offset ? read_limit-request_offset : request_count;
    always @(posedge clk) begin
        if(!rst_n) begin
            active_config<=DEFAULT_CONFIG;shadow<=0;
            shadow_valid<=0;period<=CLOCK_HZ/115200;shadow_period<=0;session_id<=0;config_id<=1;
            arm<=0;stop<=0;release_capture<=0;command_done<=0;command_status<=0;
            body_length<=0;body_snapshot<=0;stream_response<=0;read_offset<=0;reading_events<=0;
            command_phase<=0;division_remainder<=0;division_quotient<=0;division_denominator<=1;division_left<=0;
        end else begin
            arm<=0;stop<=0;release_capture<=0;command_done<=0;
            if(command_phase==1) begin
                division_remainder<=division_subtract ? trial_remainder-division_denominator : trial_remainder;
                division_quotient<=next_quotient;division_left<=division_left-1'b1;
                if(division_left==1) begin
                    shadow_period<=next_quotient;shadow_valid<=1;command_phase<=0;command_done<=1;
                end
            end else if(command_phase==2) begin
                if(state==4) begin body_snapshot<=4;body_length<=1;command_phase<=0;command_done<=1;end
            end else if(command_phase==3) begin
                if(state==0) begin command_phase<=0;command_done<=1;end
            end else if(command_valid) begin
                command_status<=OK;body_length<=0;body_snapshot<=0;stream_response<=0;command_done<=1;
                case(command)
                    8'h02:if(argument_length!=0) reply_error(BAD_LENGTH);
                        else begin body_length<=34;body_snapshot[271:0]<={FIRMWARE_ID,16'd1024,32'd15,
                            8'd1,8'd1,8'd8,DEPTH,CLOCK_HZ,8'd1};end
                    8'h12:if(argument_length!=0) reply_error(BAD_LENGTH);
                        else begin body_length<=38;body_snapshot[303:0]<={active_config,config_id};end
                    8'h23:if(argument_length!=0) reply_error(BAD_LENGTH);
                        else begin body_length<=30;body_snapshot[239:0]<={cancelled_count,timeout_count,early_count,
                            pass_count,accepted_count,config_id,session_id,{7'd0,target_ready},{5'd0,reported_state}};end
                    8'h10:if(argument_length!=34) reply_error(BAD_LENGTH);
                        else if(state!=0) reply_error(BUSY);
                        else if(!config_valid) reply_error(INVALID_ARGUMENT);
                        else begin
                            shadow<=arguments;division_quotient<=CLOCK_HZ;division_remainder<=0;
                            division_denominator<=cfg_baud;division_left<=32;command_phase<=1;command_done<=0;
                        end
                    8'h11:if(argument_length!=0) reply_error(BAD_LENGTH);
                        else if(state!=0) reply_error(BUSY);
                        else if(!shadow_valid) reply_error(INVALID_ARGUMENT);
                        else if(config_id==32'hffffffff) reply_error(INTERNAL_ERROR);
                        else begin active_config<=shadow;period<=shadow_period;shadow_valid<=0;config_id<=config_id+1'b1;
                            body_length<=4;body_snapshot[31:0]<=config_id+1'b1;end
                    8'h20:if(argument_length!=4) reply_error(BAD_LENGTH);
                        else if(state!=0) reply_error(BUSY);
                        else if(request_session!=config_id) reply_error(CONFIG_MISMATCH);
                        else if(session_id==32'hffffffff) reply_error(INTERNAL_ERROR);
                        else begin session_id<=session_id+1'b1;arm<=1;body_length<=8;
                            body_snapshot[63:0]<={config_id,session_id+1'b1};end
                    8'h21:if(argument_length!=4) reply_error(BAD_LENGTH);
                        else if(request_session!=session_id) reply_error(STALE_SESSION);
                        else if(active) begin stop<=1;command_phase<=2;command_done<=0;end
                        else begin body_length<=1;body_snapshot[7:0]<={5'd0,state};end
                    8'h22:if(argument_length!=4) reply_error(BAD_LENGTH);
                        else if(request_session!=session_id) reply_error(STALE_SESSION);
                        else if(state!=4) reply_error(NOT_READY);
                        else begin body_length<=132;body_snapshot<=metadata;end
                    8'h32:if(argument_length!=4) reply_error(BAD_LENGTH);
                        else if(request_session!=session_id) reply_error(STALE_SESSION);
                        else if(state!=4) reply_error(NOT_READY);
                        else begin release_capture<=1;command_phase<=3;command_done<=0;end
                    8'h30,8'h31:if(argument_length!=10) reply_error(BAD_LENGTH);
                        else if(request_session!=session_id) reply_error(STALE_SESSION);
                        else if(state!=4) reply_error(NOT_READY);
                        else if(request_offset>read_limit || request_count==0 ||
                            request_count>(command==8'h30 ? 992 : 1004) ||
                            (command==8'h30 && (request_offset[3:0]!=0 || request_count[3:0]!=0))) reply_error(INVALID_ARGUMENT);
                        else begin
                            read_offset<=request_offset;reading_events<=command==8'h30;stream_response<=1;
                            body_length<=10+clipped_count;body_snapshot[79:0]<={clipped_count,request_offset,session_id};
                        end
                    default:reply_error(UNKNOWN_COMMAND);
                endcase
            end
        end
    end
endmodule
