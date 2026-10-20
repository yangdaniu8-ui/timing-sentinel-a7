`timescale 1ns/1ps
// Profile5: one UART decoder, four genuinely parallel rules, one evidence ring.
// First real trigger locks new A, but all existing transactions finish through
// the post-window. A pending rule cut by freeze is INCOMPLETE, never TIMEOUT.
(* use_dsp="no" *) module mvp5_capture #(
    parameter integer CLOCK_HZ=50000000, parameter integer ADDR_BITS=16,
    parameter integer DEPTH=1<<ADDR_BITS,
    parameter [127:0] FIRMWARE_ID=128'h000000302e312e302d37612d3570766d
) (
    input wire clk,input wire rst_n,input wire [7:0] raw_inputs,output wire target_ready,
    input wire command_valid,input wire [7:0] command,input wire [15:0] argument_length,
    input wire [799:0] arguments,output reg command_done,output reg [15:0] command_status,
    output reg [10:0] body_length,output wire [3295:0] body_snapshot,
    input wire [10:0] body_byte_index,output wire [7:0] body_byte,
    output reg stream_response,input wire [10:0] stream_index,output wire [7:0] stream_data,
    output wire [2:0] state,output reg [31:0] session_id,output reg [31:0] config_id,
    // Read-only panel taps: no feedback into capture, timing or protocol.
    output wire panel_arm,output wire panel_profile_ok,output wire panel_have_a,
    output wire panel_gpio_response,output wire [1:0] panel_have_result,
    output wire [3:0] panel_gpio_result,output wire [3:0] panel_spi_result
);
    localparam [31:0] DEFAULT_PRE=DEPTH/2-1,DEFAULT_POST=DEPTH/2;
    localparam [159:0] DEFAULT_RULE={48'd100000,48'd25000,24'd0,8'd2,8'hff,8'ha5,8'd0,8'd1};
    localparam [799:0] DEFAULT_CONFIG={480'd0,DEFAULT_RULE,DEFAULT_POST,DEFAULT_PRE,
        16'd0,16'd5,32'd115200,8'd0,8'd1,8'd0,8'd5};
    localparam [15:0] OK=0,UNKNOWN_COMMAND=2,BAD_LENGTH=3,INVALID_ARGUMENT=4,
        BUSY=5,STALE_SESSION=6,NOT_READY=8,INTERNAL_ERROR=12,CONFIG_MISMATCH=14;
    reg [799:0] active_config,shadow,captured_config;
    localparam RESPONSE_SHORT=0,RESPONSE_INFO=1,RESPONSE_CONFIG=2,RESPONSE_META=3;
    reg [1:0] response_source;
    reg [1007:0] short_snapshot;
    // The protocol serializes one response before issuing another command.
    // CONFIG cannot change during that interval; META is exposed only after
    // freeze finalization. Live STATUS must still be latched atomically.
    assign body_snapshot=response_source==RESPONSE_INFO ?
        {3024'd0,FIRMWARE_ID,16'd1024,32'd15,8'd4,8'd1,8'd8,DEPTH,CLOCK_HZ,8'd5} :
        response_source==RESPONSE_CONFIG ? {2464'd0,active_config,config_id} :
        response_source==RESPONSE_META ? metadata : {2288'd0,short_snapshot};
    // Select a byte BEFORE choosing the response source. The legacy full
    // snapshot remains for interface/backpressure checks but is unused by
    // the optimized top's serializer and can disappear in synthesis.
    wire [271:0] info_snapshot={FIRMWARE_ID,16'd1024,32'd15,8'd4,8'd1,8'd8,DEPTH,CLOCK_HZ,8'd5};
    wire [831:0] config_snapshot={active_config,config_id};
    wire [7:0] info_byte=body_byte_index<11'd34 ? info_snapshot[body_byte_index[5:0]*8+:8] : 8'd0;
    wire [7:0] config_byte=body_byte_index<11'd104 ? config_snapshot[body_byte_index[6:0]*8+:8] : 8'd0;
    wire [7:0] metadata_byte=body_byte_index<11'd412 ? metadata[body_byte_index[8:0]*8+:8] : 8'd0;
    wire [7:0] short_byte=body_byte_index<11'd126 ? short_snapshot[body_byte_index[6:0]*8+:8] : 8'd0;
    assign body_byte=response_source==RESPONSE_INFO ? info_byte :
        response_source==RESPONSE_CONFIG ? config_byte :
        response_source==RESPONSE_META ? metadata_byte : short_byte;
    reg shadow_valid,arm,stop,release_capture;
    reg [31:0] period,shadow_period;
    reg [2:0] command_phase,previous_state;
    reg [31:0] division_remainder,division_quotient,division_denominator;
    reg [5:0] division_left;
    wire [32:0] trial={division_remainder,division_quotient[31]};
    wire subtract=trial>={1'b0,division_denominator};
    wire [31:0] next_quotient={division_quotient[30:0],subtract};
    wire [31:0] cfg_baud=arguments[32+:32],cfg_pre=arguments[96+:32],cfg_post=arguments[128+:32];
    wire [15:0] cfg_divisor=arguments[64+:16];
    reg config_valid; integer c;
    always @* begin
        config_valid=arguments[7:0]==5 && arguments[15:8]<8 &&
            arguments[23:16]>0 && arguments[23:20]==0 && arguments[31:27]==0 &&
            cfg_baud>=1200 && cfg_baud<=1000000 && arguments[95:89]==0 &&
            (cfg_divisor==1 || cfg_divisor==5 || cfg_divisor==10 || cfg_divisor==50) &&
            cfg_pre<DEPTH && cfg_post>=1 && cfg_post<DEPTH &&
            {1'b0,cfg_pre}+{1'b0,cfg_post}+1==DEPTH;
        if(arguments[24]) begin
            if(arguments[82:80]==arguments[85:83] || arguments[82:80]==arguments[88:86] ||
                arguments[85:83]==arguments[88:86] || arguments[82:80]==arguments[10:8] ||
                arguments[85:83]==arguments[10:8] || arguments[88:86]==arguments[10:8]) config_valid=0;
        end else if(arguments[31:24]!=0 || arguments[95:80]!=0) config_valid=0;
        for(c=0;c<4;c=c+1) begin
            if(arguments[16+c]) begin
                if(arguments[160+c*160+:8]>=8 ||
                    (arguments[200+c*160+:8]==0 && arguments[160+c*160+:8]==arguments[15:8]) ||
                    arguments[168+c*160+:8]>1 || arguments[192+c*160+:8]>2 ||
                    (arguments[176+c*160+:8]&~arguments[184+c*160+:8])!=0 ||
                    arguments[200+c*160+:8]>1 ||
                    (arguments[200+c*160+:8]==0 && arguments[208+c*160+:16]!=0) ||
                    (arguments[200+c*160+:8]==1 && (!arguments[24] || arguments[160+c*160+:16]!=0 ||
                        (arguments[208+c*160+:8]&~arguments[216+c*160+:8])!=0)) ||
                    arguments[224+c*160+:48]>arguments[272+c*160+:48] || arguments[319+c*160]) config_valid=0;
            end else if(arguments[160+c*160+:160]!=0) config_valid=0;
        end
    end
    reg [47:0] tick,selected_a[0:3],selected_b[0:3],observed_b[0:3],terminal_tick[0:3];
    reg [3:0] have_a,have_b,have_observed,have_result,trigger_locked_mask,incomplete_mask;
    reg trigger_locked;reg [7:0] primary_rule;
    reg [3:0] final_result[0:3];
    reg [31:0] counts[0:27];
    wire [7:0] levels,rising,falling,uart_byte,spi_byte;
    wire spi_valid,spi_error;
    wire [3:0] enabled=active_config[19:16];
    wire [2:0] uart_channel=active_config[10:8];
    wire [15:0] divisor=active_config[64+:16];
    wire ready,trigger_valid,manual_stop,read_valid,command_error,uart_valid,uart_error;
    wire [31:0] sample_count,trigger_index;
    wire [47:0] first_tick,anchor_tick,decision_tick;
    wire [3:0] busy,accepted,overlap,result_valid,matched_b,gpio_edge,trigger_requests;
    wire [3:0] result[0:3];wire [47:0] result_a[0:3],result_b[0:3];
    wire [2:0] reported_state=trigger_locked && state==2 ? 3'd3 : state;
    wire active=state>=1 && state<=3;
    wire first_trigger=|trigger_requests && !trigger_locked && !stop;
    assign panel_arm=arm;
    assign panel_have_a=have_a[0];
    assign panel_have_result={have_result[2],have_result[0]};
    assign panel_gpio_result=final_result[0];
    assign panel_spi_result=final_result[2];
    assign panel_gpio_response=active && have_a[0] && rising[1] && !stop;
    // Established demonstration and explicit real-task profiles have panel semantics.
    // R1 and R3 intentionally TIMEOUT in the normal scene and are not alarms.
    diagnostic_profile panel_profile(.config_bytes(active_config),.qualified(panel_profile_ok));
    assign target_ready=ready && !trigger_locked && !stop;
    input_frontend frontend(.clk(clk),.rst_n(rst_n),.clear_history(arm || state==1),
        .raw(raw_inputs),.levels(levels),.rising(rising),.falling(falling));
    target_uart_monitor monitor(.clk(clk),.rst_n(rst_n),.clear(arm || state==1 || !active),
        .serial_level(levels[uart_channel]),.bit_period(period),.byte_valid(uart_valid),
        .frame_error(uart_error),.data(uart_byte));
    target_spi_monitor spi_monitor(.clk(clk),.rst_n(rst_n),
        .clear(arm || state==1 || !active || !active_config[24]),
        .sck(levels[active_config[82:80]]),.mosi(levels[active_config[85:83]]),
        .cs_n(levels[active_config[88:86]]),.mode(active_config[26:25]),
        .byte_valid(spi_valid),.frame_error(spi_error),.data(spi_byte));
    genvar r;
    generate for(r=0;r<4;r=r+1) begin:rules
        wire [159:0] cfg=active_config[160+r*160+:160];
        wire [2:0] channel=cfg[2:0];
        // The legacy engine's gpio_edge port is a generic already-timestamped B.
        assign gpio_edge[r]=cfg[40] ? (spi_valid && (spi_byte & cfg[56+:8])==cfg[48+:8]) :
            (cfg[8] ? falling[channel] : rising[channel]);
        uart_gpio_rule engine(.clk(clk),.rst_n(rst_n),.enable(enabled[r] && (state==2 || state==3)),
            .cancel(arm || stop || state==4),.logic_tick(tick),.uart_valid(uart_valid && target_ready),
            .uart_data(uart_byte),.byte_value(cfg[16+:8]),.byte_mask(cfg[24+:8]),.gpio_edge(gpio_edge[r]),
            .min_ticks(cfg[64+:48]),.max_ticks(cfg[112+:48]),.busy(busy[r]),.accepted(accepted[r]),
            .overlap(overlap[r]),.result_valid(result_valid[r]),.result(result[r]),
            .result_a(result_a[r]),.result_b(result_b[r]),.matched_b(matched_b[r]));
        assign trigger_requests[r]=result_valid[r] && (cfg[32+:8]==2 || (cfg[32+:8]==1 && result[r]!=1));
    end endgenerate
    reg [31:0] read_offset;reg reading_events;
    wire [31:0] address=read_offset+stream_index;
    wire [7:0] sample_data;
    capture_buffer #(.ADDR_BITS(ADDR_BITS)) samples(.clk(clk),.rst_n(rst_n),.sample_data(levels),
        .logic_tick(tick),.arm(arm),.stop(stop),.release_capture(release_capture),
        .sample_divisor(divisor),.pre_count(active_config[96+:32]),.post_count(active_config[128+:32]),
        .trigger(first_trigger),.state(state),.ready(ready),.command_error(command_error),
        .valid_count(sample_count),.trigger_valid(trigger_valid),.manual_stop(manual_stop),
        .first_tick(first_tick),.anchor_tick(anchor_tick),.decision_tick(decision_tick),.trigger_index(trigger_index),
        .read_enable(state==4),.read_address(address[ADDR_BITS-1:0]),.read_data(sample_data),.read_valid(read_valid));
    wire [15:0] event_count;wire [31:0] event_overwrites;wire [127:0] event_word;wire event_read_valid;
    wire [15:0] event_results={stop && busy[3] ? 4'd4 : result[3],stop && busy[2] ? 4'd4 : result[2],
        stop && busy[1] ? 4'd4 : result[1],stop && busy[0] ? 4'd4 : result[0]};
    wire event_enable=(state==2 || state==3) &&
        (uart_valid || uart_error || spi_valid || spi_error || |rising || |falling || |result_valid || |overlap || (stop && |busy));
    wire [127:0] event_record={6'd0,spi_error,spi_valid ? spi_byte : 8'd0,spi_valid,{3'd0,state},accepted,overlap,event_results,
        uart_error,uart_byte,uart_valid,falling,rising,levels,tick};
    event_buffer events(.clk(clk),.rst_n(rst_n),.clear(arm),.write_enable(event_enable),.event_data(event_record),
        .count(event_count),.overwrites(event_overwrites),.frozen(state==4),.read_enable(state==4),
        .read_address(address[13:4]),.read_data(event_word),.read_valid(event_read_valid));
    assign stream_data=reading_events ? (event_read_valid ? event_word>>(address[3:0]*8) : 8'd0) : sample_data;
    function [47:0] span;
        // sample_count is0..DEPTH; nonempty evidence uses count-1.
        // At most (DEPTH-1)*50 ticks, then zero-extend to the48-bit timebase.
        input [ADDR_BITS-1:0] count;input [15:0] div;reg [ADDR_BITS+5:0] n;
        begin n=count;case(div) 1:span=n;5:span=(n<<2)+n;10:span=(n<<3)+(n<<1);
            50:span=(n<<5)+(n<<4)+(n<<1);default:span=0;endcase end
    endfunction
    function [7:0] lowest_rule;
        input [3:0] mask;
        begin if(mask[0]) lowest_rule=0;else if(mask[1]) lowest_rule=1;
            else if(mask[2]) lowest_rule=2;else if(mask[3]) lowest_rule=3;else lowest_rule=255;end
    endfunction
    integer k,j; reg [3295:0] metadata;reg [1007:0] status_snapshot;
    reg [3:0] observed_valid;
    always @* begin
        metadata=0;status_snapshot=0;observed_valid=0;
        metadata[0+:8]=5;metadata[8+:8]={6'd0,manual_stop,trigger_valid};
        metadata[16+:8]=trigger_valid ? primary_rule : 8'hff;metadata[24+:8]={4'd0,enabled};
        metadata[32+:32]=session_id;metadata[64+:32]=config_id;metadata[96+:32]=CLOCK_HZ;
        metadata[128+:32]=sample_count;metadata[160+:16]=divisor;metadata[176+:16]=event_count;
        metadata[192+:32]=event_overwrites;metadata[224+:48]=first_tick;
        metadata[272+:48]=trigger_valid ? anchor_tick : 48'd0;
        metadata[320+:48]=trigger_valid ? decision_tick : 48'd0;metadata[368+:32]=trigger_index;
        metadata[400+:8]=trigger_valid ? {4'd0,trigger_locked_mask} : 8'd0;
        metadata[408+:8]={4'd0,incomplete_mask};metadata[2496+:800]=captured_config;
        status_snapshot[0+:8]={5'd0,reported_state};status_snapshot[8+:8]={7'd0,target_ready};
        status_snapshot[16+:32]=session_id;status_snapshot[48+:32]=config_id;
        status_snapshot[80+:8]={4'd0,enabled};status_snapshot[88+:8]=trigger_locked ? primary_rule : 8'hff;
        status_snapshot[96+:8]={4'd0,trigger_locked_mask};
        for(k=0;k<4;k=k+1) begin
            observed_valid[k]=have_observed[k] && sample_count!=0 &&
                (observed_b[k]-first_tick)<=span(sample_count-1,divisor);
            metadata[448+k*512+:8]={4'd0,have_result[k],observed_valid[k],have_b[k],have_a[k]};
            metadata[456+k*512+:8]={4'd0,final_result[k]};
            metadata[480+k*512+:48]=have_a[k] ? selected_a[k] : 48'd0;
            metadata[528+k*512+:48]=have_b[k] ? selected_b[k] : 48'd0;
            metadata[576+k*512+:48]=have_a[k] ? selected_a[k]+active_config[272+k*160+:48] : 48'd0;
            metadata[624+k*512+:48]=observed_valid[k] ? observed_b[k] : 48'd0;
            metadata[672+k*512+:48]=have_result[k] ? terminal_tick[k] : 48'd0;
            for(j=0;j<7;j=j+1) begin
                metadata[736+k*512+j*32+:32]=counts[k*7+j];
                status_snapshot[112+k*224+j*32+:32]=counts[k*7+j];
            end
        end
    end
    integer s,n;
    always @(posedge clk) begin
        if(!rst_n) begin
            tick<=1000;previous_state<=0;have_a<=0;have_b<=0;have_observed<=0;have_result<=0;
            trigger_locked<=0;trigger_locked_mask<=0;primary_rule<=255;incomplete_mask<=0;captured_config<=DEFAULT_CONFIG;
            for(s=0;s<4;s=s+1) begin selected_a[s]<=0;selected_b[s]<=0;observed_b[s]<=0;
                terminal_tick[s]<=0;final_result[s]<=0;end
            for(n=0;n<28;n=n+1) counts[n]<=0;
        end else begin
            tick<=tick+1'b1;previous_state<=state;
            if(arm) begin
                have_a<=0;have_b<=0;have_observed<=0;have_result<=0;trigger_locked<=0;
                trigger_locked_mask<=0;primary_rule<=255;incomplete_mask<=0;captured_config<=active_config;
                for(s=0;s<4;s=s+1) begin selected_a[s]<=0;selected_b[s]<=0;observed_b[s]<=0;
                    terminal_tick[s]<=0;final_result[s]<=0;end
                for(n=0;n<28;n=n+1) counts[n]<=0;
            end else begin
                if(first_trigger) begin trigger_locked<=1;trigger_locked_mask<=trigger_requests;
                    primary_rule<=lowest_rule(trigger_requests);end
                for(s=0;s<4;s=s+1) begin
                    if(active && enabled[s]) begin
                        if(accepted[s]) begin
                            if(counts[s*7]!=32'hffffffff) counts[s*7]<=counts[s*7]+1'b1;
                            selected_a[s]<=tick;have_a[s]<=1;have_b[s]<=0;selected_b[s]<=0;
                            have_observed[s]<=0;observed_b[s]<=0;have_result[s]<=0;terminal_tick[s]<=0;final_result[s]<=0;
                        end
                        if(gpio_edge[s] && !stop && (have_a[s] || accepted[s]) && (!have_observed[s] || accepted[s])) begin
                            observed_b[s]<=tick;have_observed[s]<=1;
                        end
                        if(overlap[s] && counts[s*7+6]!=32'hffffffff) counts[s*7+6]<=counts[s*7+6]+1'b1;
                        if(result_valid[s]) begin
                            final_result[s]<=result[s];selected_a[s]<=result_a[s];selected_b[s]<=result_b[s];
                            have_a[s]<=1;have_b[s]<=matched_b[s];have_result[s]<=1;terminal_tick[s]<=tick;
                            if(result[s]==1 && counts[s*7+1]!=32'hffffffff) counts[s*7+1]<=counts[s*7+1]+1'b1;
                            if(result[s]==2 && counts[s*7+2]!=32'hffffffff) counts[s*7+2]<=counts[s*7+2]+1'b1;
                            if(result[s]==3 && counts[s*7+3]!=32'hffffffff) counts[s*7+3]<=counts[s*7+3]+1'b1;
                        end
                        if(stop && busy[s]) begin
                            final_result[s]<=4;have_result[s]<=1;terminal_tick[s]<=tick;have_b[s]<=0;selected_b[s]<=0;
                            if(counts[s*7+4]!=32'hffffffff) counts[s*7+4]<=counts[s*7+4]+1'b1;
                        end
                    end
                    if(state==4 && previous_state!=4 && busy[s]) begin
                        final_result[s]<=5;have_result[s]<=1;terminal_tick[s]<=tick;have_b[s]<=0;selected_b[s]<=0;
                        incomplete_mask[s]<=1;
                        if(counts[s*7+5]!=32'hffffffff) counts[s*7+5]<=counts[s*7+5]+1'b1;
                    end
                end
            end
        end
    end
    task reply_error;
        input [15:0] status;
        begin command_status<=status;body_length<=0;short_snapshot<=0;response_source<=RESPONSE_SHORT;stream_response<=0;command_done<=1;end
    endtask
    wire [31:0] request_session=arguments[31:0],request_offset=arguments[63:32];
    wire [15:0] request_count=arguments[79:64];
    wire [31:0] read_limit=command==8'h30 ? {16'd0,event_count}<<4 : sample_count;
    wire [15:0] clipped_count=request_count>read_limit-request_offset ? read_limit-request_offset : request_count;
    always @(posedge clk) begin
        if(!rst_n) begin
            active_config<=DEFAULT_CONFIG;shadow<=0;shadow_valid<=0;period<=CLOCK_HZ/115200;shadow_period<=0;
            session_id<=0;config_id<=1;arm<=0;stop<=0;release_capture<=0;command_done<=0;
            command_status<=0;body_length<=0;short_snapshot<=0;response_source<=RESPONSE_SHORT;stream_response<=0;read_offset<=0;reading_events<=0;
            command_phase<=0;division_remainder<=0;division_quotient<=0;division_denominator<=1;division_left<=0;
        end else begin
            arm<=0;stop<=0;release_capture<=0;command_done<=0;
            case(command_phase)
                1:begin
                    division_remainder<=subtract ? trial-division_denominator : trial;
                    division_quotient<=next_quotient;division_left<=division_left-1'b1;
                    if(division_left==1) begin shadow_period<=next_quotient;shadow_valid<=1;command_phase<=0;command_done<=1;end
                end
                2:if(state==4 && previous_state==4) begin short_snapshot<=4;body_length<=1;command_phase<=0;command_done<=1;end
                3:if(state==0) begin command_phase<=0;command_done<=1;end
                4:begin response_source<=RESPONSE_META;body_length<=412;command_phase<=0;command_done<=1;end
                5:begin short_snapshot<=status_snapshot;body_length<=126;command_phase<=0;command_done<=1;end
                default:if(command_valid) begin
                    command_status<=OK;body_length<=0;short_snapshot<=0;response_source<=RESPONSE_SHORT;stream_response<=0;command_done<=1;
                    case(command)
                        8'h02:if(argument_length!=0) reply_error(BAD_LENGTH);
                            else begin body_length<=34;response_source<=RESPONSE_INFO;end
                        8'h12:if(argument_length!=0) reply_error(BAD_LENGTH);
                            else begin body_length<=104;response_source<=RESPONSE_CONFIG;end
                        8'h23:if(argument_length!=0) reply_error(BAD_LENGTH);
                            else begin body_length<=126;short_snapshot<=status_snapshot;
                                if(state==4 && previous_state!=4) begin command_phase<=5;command_done<=0;end end
                        8'h10:if(argument_length!=100) reply_error(BAD_LENGTH);
                            else if(state!=0) reply_error(BUSY);
                            else if(!config_valid) reply_error(INVALID_ARGUMENT);
                            else begin shadow<=arguments;division_quotient<=CLOCK_HZ;division_remainder<=0;
                                division_denominator<=cfg_baud;division_left<=32;command_phase<=1;command_done<=0;end
                        8'h11:if(argument_length!=0) reply_error(BAD_LENGTH);
                            else if(state!=0) reply_error(BUSY);
                            else if(!shadow_valid) reply_error(INVALID_ARGUMENT);
                            else if(config_id==32'hffffffff) reply_error(INTERNAL_ERROR);
                            else begin active_config<=shadow;period<=shadow_period;shadow_valid<=0;config_id<=config_id+1'b1;
                                body_length<=4;short_snapshot[31:0]<=config_id+1'b1;end
                        8'h20:if(argument_length!=4) reply_error(BAD_LENGTH);
                            else if(state!=0) reply_error(BUSY);
                            else if(request_session!=config_id) reply_error(CONFIG_MISMATCH);
                            else if(session_id==32'hffffffff) reply_error(INTERNAL_ERROR);
                            else begin session_id<=session_id+1'b1;arm<=1;body_length<=8;short_snapshot[63:0]<={config_id,session_id+1'b1};end
                        8'h21:if(argument_length!=4) reply_error(BAD_LENGTH);
                            else if(request_session!=session_id) reply_error(STALE_SESSION);
                            else if(active) begin stop<=1;command_phase<=2;command_done<=0;end
                            else begin body_length<=1;short_snapshot[7:0]<={5'd0,state};end
                        8'h22:if(argument_length!=4) reply_error(BAD_LENGTH);
                            else if(request_session!=session_id) reply_error(STALE_SESSION);
                            else if(state!=4) reply_error(NOT_READY);
                            else begin body_length<=412;response_source<=RESPONSE_META;
                                if(previous_state!=4) begin command_phase<=4;command_done<=0;end end
                        8'h32:if(argument_length!=4) reply_error(BAD_LENGTH);
                            else if(request_session!=session_id) reply_error(STALE_SESSION);
                            else if(state!=4) reply_error(NOT_READY);
                            else begin release_capture<=1;command_phase<=3;command_done<=0;end
                        8'h30,8'h31:if(argument_length!=10) reply_error(BAD_LENGTH);
                            else if(request_session!=session_id) reply_error(STALE_SESSION);
                            else if(state!=4) reply_error(NOT_READY);
                            else if(request_offset>read_limit || request_count==0 || request_count>(command==8'h30 ? 992 : 1004) ||
                                (command==8'h30 && (request_offset[3:0]!=0 || request_count[3:0]!=0))) reply_error(INVALID_ARGUMENT);
                            else begin read_offset<=request_offset;reading_events<=command==8'h30;stream_response<=1;
                                body_length<=10+clipped_count;short_snapshot[79:0]<={clipped_count,request_offset,session_id};end
                        default:reply_error(UNKNOWN_COMMAND);
                    endcase
                end
            endcase
        end
    end
endmodule
