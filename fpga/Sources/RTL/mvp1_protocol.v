`timescale 1ns/1ps
// TS/v1 framing/cache from the verified link transport, separated so EXT1 stays
// immutable. Only CRC-valid, new ordinary requests reach the command backend.
module mvp1_protocol #(
    parameter integer FRAME_TIMEOUT_CYCLES=12500000
) (
    input wire clk, input wire rst_n,
    input wire [7:0] rx_data, input wire rx_valid, output wire rx_ready,
    output wire [7:0] tx_data, output wire tx_valid, input wire tx_ready,
    output wire synced, output reg protocol_error,
    output reg command_valid, output wire [7:0] command,
    output wire [15:0] argument_length, output reg [271:0] arguments,
    input wire command_done, input wire [15:0] command_status,
    input wire [10:0] body_length, input wire [1055:0] body_snapshot,
    input wire stream_response, output wire [10:0] stream_index,
    input wire [7:0] stream_data
);
    localparam MAGIC_T=0,MAGIC_S=1,HEADER=2,PAYLOAD=3,CRC_LO=4,CRC_HI=5,
        DISPATCH=6,COMPARE=7,EXECUTE=8,COPY_REQUEST=9,BUILD=10,SEND=11,
        BUILD_WAIT=12,COMMAND_WAIT=13;
    localparam OK=0,BAD_LENGTH=3,INVALID_ARGUMENT=4,ID_CONFLICT=7,
        NOT_SYNCED=9,STALE_LINK=10,STALE_REQUEST=13;
    reg [3:0] state;
    reg [2:0] header_index;
    reg [10:0] payload_index,work_index,frame_index,send_length,cache_length;
    reg [15:0] request_length,response_length,response_status,rx_crc,tx_crc;
    reg [7:0] request_command,received_crc_lo,last_command;
    reg [31:0] request_id,last_id,frame_age;
    reg [63:0] request_token,link_token;
    reg [15:0] last_length;
    reg cache_valid,cache_new,replay,ping_body,stream_body;
    reg [1055:0] snapshot;
    (* ram_style="distributed" *) reg [7:0] request_memory[0:1023];
    (* ram_style="distributed" *) reg [7:0] last_memory[0:1023];
    (* ram_style="distributed" *) reg [7:0] frame_memory[0:1035];
    (* ram_style="distributed" *) reg [7:0] cache_memory[0:1035];
    reg [7:0] build_byte;
    integer body_offset;
    assign command=request_command;
    assign argument_length=request_length>=8 ? request_length-8 : 0;
    assign stream_index=frame_index-30;
    assign synced=link_token!=0;
    assign rx_ready=state<=CRC_HI && (state==MAGIC_T || frame_age<FRAME_TIMEOUT_CYCLES-1);
    assign tx_valid=state==SEND;
    assign tx_data=replay ? cache_memory[frame_index] : frame_memory[frame_index];
    function [15:0] crc_byte;
        input [15:0] crc; input [7:0] value;
        reg [15:0] c; integer b;
        begin
            c=crc^{value,8'd0};
            for(b=0;b<8;b=b+1) c=c[15] ? (c<<1)^16'h1021 : c<<1;
            crc_byte=c;
        end
    endfunction
    always @(posedge clk) begin
        if(rst_n && state==PAYLOAD && rx_valid && rx_ready) request_memory[payload_index]<=rx_data;
        if(rst_n && state==COPY_REQUEST) last_memory[work_index]<=request_memory[work_index];
        if(rst_n && state==BUILD) begin
            frame_memory[frame_index]<=build_byte;
            if(cache_new) cache_memory[frame_index]<=build_byte;
        end
    end
    always @* begin
        build_byte=0;body_offset=frame_index-20;
        case(frame_index)
            0:build_byte=8'h54;1:build_byte=8'h53;2:build_byte=1;
            3:build_byte=request_command|8'h80;
            4,5,6,7:build_byte=request_id>>((frame_index-4)*8);
            8:build_byte=response_length[7:0];9:build_byte=response_length[15:8];
            10,11,12,13,14,15,16,17:build_byte=request_token>>((frame_index-10)*8);
            18:build_byte=response_status[7:0];19:build_byte=response_status[15:8];
            default:begin
                if(frame_index==10+response_length) build_byte=tx_crc[7:0];
                else if(frame_index==11+response_length) build_byte=tx_crc[15:8];
                else if(frame_index>=20 && frame_index<10+response_length) begin
                    if(ping_body) build_byte=request_memory[body_offset+8];
                    else if(stream_body && body_offset>=10) build_byte=stream_data;
                    else build_byte=snapshot>>(body_offset*8);
                end
            end
        endcase
    end
    task error_reply;
        input [15:0] status;
        begin
            response_status<=status;response_length<=10;
            ping_body<=0;stream_body<=0;cache_new<=0;replay<=0;
            frame_index<=0;tx_crc<=16'hffff;state<=BUILD;
        end
    endtask
    always @(posedge clk) begin
        if(!rst_n) begin
            state<=MAGIC_T;header_index<=0;payload_index<=0;work_index<=0;frame_index<=0;
            request_length<=0;response_length<=0;response_status<=0;rx_crc<=16'hffff;tx_crc<=16'hffff;
            request_command<=0;received_crc_lo<=0;last_command<=0;request_id<=0;last_id<=0;
            frame_age<=0;request_token<=0;link_token<=0;last_length<=0;send_length<=0;cache_length<=0;
            cache_valid<=0;cache_new<=0;replay<=0;ping_body<=0;stream_body<=0;snapshot<=0;
            command_valid<=0;protocol_error<=0;arguments<=0;
        end else begin
            command_valid<=0;protocol_error<=0;
            if(state>=MAGIC_S && state<=CRC_HI) frame_age<=frame_age+1'b1;else frame_age<=0;
            if(state>=MAGIC_S && state<=CRC_HI && frame_age>=FRAME_TIMEOUT_CYCLES-1) begin
                state<=MAGIC_T;protocol_error<=1;
            end else case(state)
                MAGIC_T:if(rx_valid && rx_ready && rx_data==8'h54) begin state<=MAGIC_S;frame_age<=0;end
                MAGIC_S:if(rx_valid && rx_ready) begin
                    if(rx_data==8'h53) begin
                        state<=HEADER;header_index<=0;rx_crc<=16'hffff;request_id<=0;
                        request_length<=0;request_token<=0;arguments<=0;
                    end else if(rx_data==8'h54) frame_age<=0;else state<=MAGIC_T;
                end
                HEADER:if(rx_valid && rx_ready) begin
                    rx_crc<=crc_byte(rx_crc,rx_data);header_index<=header_index+1'b1;
                    case(header_index)
                        0:if(rx_data!=1) begin state<=MAGIC_T;protocol_error<=1;end
                        1:request_command<=rx_data;
                        2,3,4,5:request_id[(header_index-2)*8+:8]<=rx_data;
                        6:request_length[7:0]<=rx_data;
                        7:begin
                            request_length[15:8]<=rx_data;payload_index<=0;
                            if({rx_data,request_length[7:0]}>1024) begin state<=MAGIC_T;protocol_error<=1;end
                            else state<=({rx_data,request_length[7:0]}==0 ? CRC_LO : PAYLOAD);
                        end
                    endcase
                end
                PAYLOAD:if(rx_valid && rx_ready) begin
                    rx_crc<=crc_byte(rx_crc,rx_data);
                    if(payload_index<8) request_token[payload_index*8+:8]<=rx_data;
                    if(payload_index>=8 && payload_index<42) arguments[(payload_index-8)*8+:8]<=rx_data;
                    if(payload_index==request_length-1) state<=CRC_LO;else payload_index<=payload_index+1'b1;
                end
                CRC_LO:if(rx_valid && rx_ready) begin received_crc_lo<=rx_data;state<=CRC_HI;end
                CRC_HI:if(rx_valid && rx_ready) begin
                    if({rx_data,received_crc_lo}==rx_crc) state<=DISPATCH;
                    else begin state<=MAGIC_T;protocol_error<=1;end
                end
                DISPATCH:begin
                    if(request_length<8) begin request_token<=0;error_reply(BAD_LENGTH);end
                    else if(request_command==3) begin
                        if(request_id!=0 || request_length!=8 || request_token==0) error_reply(INVALID_ARGUMENT);
                        else begin
                            if(request_token!=link_token) begin link_token<=request_token;cache_valid<=0;last_id<=0;end
                            error_reply(OK);
                        end
                    end else if(link_token==0) error_reply(NOT_SYNCED);
                    else if(request_token!=link_token) error_reply(STALE_LINK);
                    else if(request_id==0) error_reply(INVALID_ARGUMENT);
                    else if(cache_valid && request_id<last_id) error_reply(STALE_REQUEST);
                    else if(cache_valid && request_id==last_id) begin
                        if(request_command!=last_command || request_length!=last_length) error_reply(ID_CONFLICT);
                        else begin work_index<=0;state<=COMPARE;end
                    end else state<=EXECUTE;
                end
                COMPARE:begin
                    if(request_memory[work_index]!=last_memory[work_index]) error_reply(ID_CONFLICT);
                    else if(work_index==request_length-1) begin
                        replay<=1;frame_index<=0;send_length<=cache_length;state<=SEND;
                    end else work_index<=work_index+1'b1;
                end
                EXECUTE:begin
                    response_status<=OK;response_length<=10;cache_new<=1;replay<=0;
                    work_index<=0;ping_body<=0;stream_body<=0;snapshot<=0;
                    if(request_command==1) begin
                        state<=COPY_REQUEST;
                        if(request_length<=1022) begin ping_body<=1;response_length<=request_length+2;end
                        else response_status<=BAD_LENGTH;
                    end else begin command_valid<=1;state<=COMMAND_WAIT;end
                end
                COMMAND_WAIT:if(command_done) begin
                    response_status<=command_status;response_length<=10+body_length;
                    snapshot<=body_snapshot;stream_body<=stream_response;state<=COPY_REQUEST;
                end
                COPY_REQUEST:begin
                    if(work_index==request_length-1) begin
                        last_command<=request_command;last_length<=request_length;last_id<=request_id;cache_valid<=1;
                        frame_index<=0;tx_crc<=16'hffff;state<=stream_body ? BUILD_WAIT : BUILD;
                    end else work_index<=work_index+1'b1;
                end
                BUILD:begin
                    if(frame_index>=2 && frame_index<10+response_length) tx_crc<=crc_byte(tx_crc,build_byte);
                    if(frame_index==11+response_length) begin
                        send_length<=12+response_length;if(cache_new) cache_length<=12+response_length;
                        frame_index<=0;state<=SEND;
                    end else begin frame_index<=frame_index+1'b1;if(stream_body) state<=BUILD_WAIT;end
                end
                BUILD_WAIT:state<=BUILD;
                SEND:if(tx_ready) begin
                    if(frame_index==send_length-1) begin frame_index<=0;state<=MAGIC_T;end
                    else frame_index<=frame_index+1'b1;
                end
                default:state<=MAGIC_T;
            endcase
        end
    end
endmodule
