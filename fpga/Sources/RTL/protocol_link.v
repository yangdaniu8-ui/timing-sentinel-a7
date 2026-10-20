`timescale 1ns/1ps

// TS/v1 transport bring-up profile 0. No sampling/rule capabilities are claimed.
// One request at a time. Cache the last exact ordinary request and raw reply.
module protocol_link #(
    parameter integer CLOCK_HZ = 50_000_000,
    parameter integer FRAME_TIMEOUT_CYCLES = CLOCK_HZ / 4,
    parameter integer ENABLE_DIAGNOSTICS = 0,
    parameter integer ENABLE_RULE_DIAGNOSTICS = 0,
    parameter integer ENABLE_EXTERNAL_DIAGNOSTICS = 0,
    parameter [127:0] FIRMWARE_ID = 128'h000000302e312e302d37612d6b6e696c
) (
    input wire clk, input wire rst_n,
    input wire [7:0] rx_data, input wire rx_valid, output wire rx_ready,
    output wire [7:0] tx_data, output wire tx_valid, input wire tx_ready,
    output wire synced, output reg protocol_error,
    input wire [511:0] diagnostic_status, input wire diagnostic_ready,
    output wire [15:0] diagnostic_address, input wire [7:0] diagnostic_data,
    input wire [31:0] diagnostic_generation,
    output reg diagnostic_start, output reg [1:0] diagnostic_case,
    output reg diagnostic_stop,
    input wire [31:0] diagnostic_event_bytes,
    output wire [13:0] diagnostic_event_address, input wire [7:0] diagnostic_event_data
);
    localparam [3:0] MAGIC_T=0, MAGIC_S=1, HEADER=2, PAYLOAD=3,
        CRC_LO=4, CRC_HI=5, DISPATCH=6, COMPARE=7, EXECUTE=8,
        COPY_REQUEST=9, BUILD=10, SEND=11, BUILD_WAIT=12;
    localparam [2:0] BODY_NONE=0, BODY_PING=1, BODY_INFO=2,
        BODY_CONFIG=3, BODY_STATUS=4, BODY_DIAGNOSTIC=5, BODY_DIAGNOSTIC_SAMPLES=6;
    localparam [15:0] OK=0, UNKNOWN_COMMAND=2, BAD_LENGTH=3,
        INVALID_ARGUMENT=4, ID_CONFLICT=7, NOT_SYNCED=9,
        STALE_LINK=10, UNSUPPORTED=11, STALE_REQUEST=13, BUSY=5;
    // ASCII "link-a7-0.1.0", NUL-padded, least-significant byte first.
    localparam [271:0] INFO_BYTES = {FIRMWARE_ID, 16'd1024, 32'd0,
        8'd0, 8'd0, 8'd0, 32'd0, 32'd50000000, 8'd0};
    // Read-only planned defaults, config_id 0: NOT an active capture config.
    localparam [271:0] CONFIG_BYTES = {48'd100000, 48'd0, 8'd0,
        8'd2, 8'hff, 8'ha5, 32'd19661, 32'd45874, 16'd5,
        32'd115200, 8'd0, 8'd1, 8'd0, 8'd1};

    reg [3:0] state;
    reg [2:0] header_index, body_kind;
    reg [10:0] payload_index, work_index, frame_index;
    reg [15:0] request_length, response_length, response_status;
    reg [15:0] rx_crc, tx_crc;
    reg [7:0] request_command, received_crc_lo;
    reg [31:0] request_id, last_id;
    reg [63:0] request_token, link_token;
    reg [31:0] frame_age;
    reg cache_valid, cache_new, replay;
    reg [7:0] last_command;
    reg [15:0] last_length;
    reg [10:0] send_length, cache_length;
    (* ram_style="distributed" *) reg [7:0] request_memory [0:1023];
    (* ram_style="distributed" *) reg [7:0] last_memory [0:1023];
    (* ram_style="distributed" *) reg [7:0] frame_memory [0:1035];
    (* ram_style="distributed" *) reg [7:0] cache_memory [0:1035];
    reg [7:0] build_byte;
    integer body_offset;
    reg [511:0] diagnostic_snapshot;
    reg [31:0] diagnostic_offset;
    reg [15:0] diagnostic_count;
    // Decode tiny diagnostic arguments while receiving. Constant-index parallel
    // reads from request_memory would turn its RAM into a multi-port FF bank.
    reg [31:0] requested_offset;
    reg [15:0] requested_count;
    reg reading_events;
    reg [31:0] read_generation;
    wire [31:0] read_limit=(ENABLE_RULE_DIAGNOSTICS && request_command==8'h07) ? diagnostic_event_bytes :
        ENABLE_EXTERNAL_DIAGNOSTICS ? diagnostic_status[95:64] : 65536;
    assign diagnostic_address = diagnostic_offset + frame_index - 30;
    assign diagnostic_event_address = diagnostic_offset + frame_index - 30;

    function [15:0] crc_byte;
        input [15:0] crc;
        input [7:0] value;
        reg [15:0] c;
        integer bitno;
        begin
            c = crc ^ {value, 8'h00};
            for (bitno=0; bitno<8; bitno=bitno+1)
                c = c[15] ? (c << 1) ^ 16'h1021 : c << 1;
            crc_byte = c;
        end
    endfunction

    assign synced = link_token != 0;
    assign rx_ready = state <= CRC_HI &&
        (state == MAGIC_T || frame_age < FRAME_TIMEOUT_CYCLES - 1);
    assign tx_valid = state == SEND;
    assign tx_data = replay ? cache_memory[frame_index] : frame_memory[frame_index];

    // No RAM-array reset; separate synchronous memory writes permit inference.
    always @(posedge clk) begin
        if (rst_n && state == PAYLOAD && rx_valid && rx_ready)
            request_memory[payload_index] <= rx_data;
        if (rst_n && state == COPY_REQUEST)
            last_memory[work_index] <= request_memory[work_index];
        if (rst_n && state == BUILD) begin
            frame_memory[frame_index] <= build_byte;
            if (cache_new) cache_memory[frame_index] <= build_byte;
        end
    end

    always @* begin
        build_byte = 0;
        body_offset = frame_index - 20;
        case (frame_index)
            0: build_byte = 8'h54;
            1: build_byte = 8'h53;
            2: build_byte = 1;
            3: build_byte = request_command | 8'h80;
            4,5,6,7: build_byte = request_id >> ((frame_index-4)*8);
            8: build_byte = response_length[7:0];
            9: build_byte = response_length[15:8];
            10,11,12,13,14,15,16,17:
                build_byte = request_token >> ((frame_index-10)*8);
            18: build_byte = response_status[7:0];
            19: build_byte = response_status[15:8];
            default: begin
                if (frame_index == 10 + response_length) build_byte = tx_crc[7:0];
                else if (frame_index == 11 + response_length) build_byte = tx_crc[15:8];
                else if (frame_index >= 20 && frame_index < 10 + response_length) begin
                    case (body_kind)
                        BODY_PING: build_byte = request_memory[body_offset + 8];
                        BODY_INFO: build_byte = INFO_BYTES >> (body_offset*8);
                        BODY_CONFIG: if (body_offset >= 4)
                            build_byte = CONFIG_BYTES >> ((body_offset-4)*8);
                        BODY_STATUS: build_byte = 0; // IDLE/not ready; zero IDs/counters.
                        BODY_DIAGNOSTIC: build_byte = diagnostic_snapshot >> (body_offset*8);
                        BODY_DIAGNOSTIC_SAMPLES: begin
                            if(body_offset<4) build_byte=read_generation >> (body_offset*8);
                            else if(body_offset>=4 && body_offset<8) build_byte=diagnostic_offset >> ((body_offset-4)*8);
                            else if(body_offset==8) build_byte=diagnostic_count[7:0];
                            else if(body_offset==9) build_byte=diagnostic_count[15:8];
                            else if(body_offset>=10) build_byte=reading_events ? diagnostic_event_data : diagnostic_data;
                        end
                        default: build_byte = 0;
                    endcase
                end
            end
        endcase
    end

    task error_reply;
        input [15:0] status_code;
        begin
            response_status <= status_code;
            response_length <= 10;
            body_kind <= BODY_NONE;
            cache_new <= 0;
            replay <= 0;
            frame_index <= 0;
            tx_crc <= 16'hffff;
            state <= BUILD;
        end
    endtask

    // The board supplies a synchronized release. Synchronous control reset
    // also prevents diagnostic read-address flops from async-toggling BRAM.
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= MAGIC_T;
            header_index <= 0; body_kind <= BODY_NONE;
            payload_index <= 0; work_index <= 0; frame_index <= 0;
            request_length <= 0; response_length <= 0; response_status <= 0;
            rx_crc <= 16'hffff; tx_crc <= 16'hffff;
            request_command <= 0; received_crc_lo <= 0;
            request_id <= 0; last_id <= 0;
            request_token <= 0; link_token <= 0;
            frame_age <= 0;
            cache_valid <= 0; cache_new <= 0; replay <= 0;
            last_command <= 0; last_length <= 0;
            send_length <= 0; cache_length <= 0;
            protocol_error <= 0;
            diagnostic_snapshot <= 0; diagnostic_offset <= 0; diagnostic_count <= 0;
            requested_offset <= 0; requested_count <= 0;
            reading_events<=0; read_generation<=1; diagnostic_start<=0; diagnostic_case<=0; diagnostic_stop<=0;
        end else begin
            protocol_error <= 0;
            diagnostic_start<=0;
            diagnostic_stop<=0;
            if (state >= MAGIC_S && state <= CRC_HI) frame_age <= frame_age + 1'b1;
            else frame_age <= 0;
            if (state >= MAGIC_S && state <= CRC_HI && frame_age >= FRAME_TIMEOUT_CYCLES - 1) begin
                state <= MAGIC_T;
                protocol_error <= 1;
            end else case (state)
                MAGIC_T: if (rx_valid && rx_ready && rx_data == 8'h54) begin
                    state <= MAGIC_S;
                    frame_age <= 0;
                end
                MAGIC_S: if (rx_valid && rx_ready) begin
                    if (rx_data == 8'h53) begin
                        state <= HEADER;
                        header_index <= 0;
                        rx_crc <= 16'hffff;
                        request_id <= 0;
                        request_length <= 0;
                        request_token <= 0;
                        requested_offset <= 0; requested_count <= 0;
                    end else if (rx_data == 8'h54) frame_age <= 0;
                    else state <= MAGIC_T;
                end
                HEADER: if (rx_valid && rx_ready) begin
                    rx_crc <= crc_byte(rx_crc, rx_data);
                    header_index <= header_index + 1'b1;
                    case (header_index)
                        0: if (rx_data != 1) begin
                            state <= MAGIC_T; protocol_error <= 1;
                        end
                        1: request_command <= rx_data;
                        2,3,4,5: request_id[(header_index-2)*8 +: 8] <= rx_data;
                        6: request_length[7:0] <= rx_data;
                        7: begin
                            request_length[15:8] <= rx_data;
                            payload_index <= 0;
                            if ({rx_data, request_length[7:0]} > 1024) begin
                                state <= MAGIC_T; protocol_error <= 1;
                            end else state <= {rx_data, request_length[7:0]} == 0 ? CRC_LO : PAYLOAD;
                        end
                    endcase
                end
                PAYLOAD: if (rx_valid && rx_ready) begin
                    rx_crc <= crc_byte(rx_crc, rx_data);
                    if (payload_index < 8) request_token[payload_index*8 +: 8] <= rx_data;
                    if (ENABLE_DIAGNOSTICS && payload_index>=8 && payload_index<12)
                        requested_offset[(payload_index-8)*8 +: 8] <= rx_data;
                    if (ENABLE_DIAGNOSTICS && payload_index>=12 && payload_index<14)
                        requested_count[(payload_index-12)*8 +: 8] <= rx_data;
                    if (payload_index == request_length-1) state <= CRC_LO;
                    else payload_index <= payload_index + 1'b1;
                end
                CRC_LO: if (rx_valid && rx_ready) begin
                    received_crc_lo <= rx_data;
                    state <= CRC_HI;
                end
                CRC_HI: if (rx_valid && rx_ready) begin
                    if ({rx_data, received_crc_lo} == rx_crc) state <= DISPATCH;
                    else begin state <= MAGIC_T; protocol_error <= 1; end
                end
                DISPATCH: begin
                    if (request_length < 8) begin
                        request_token <= 0;
                        error_reply(BAD_LENGTH);
                    end else if (request_command == 8'h03) begin
                        if (request_id != 0 || request_length != 8 || request_token == 0)
                            error_reply(INVALID_ARGUMENT);
                        else begin
                            if (request_token != link_token) begin
                                link_token <= request_token;
                                cache_valid <= 0;
                                last_id <= 0;
                            end
                            error_reply(OK); // Same-token SYNC preserves the ordinary cache.
                        end
                    end else if (link_token == 0) error_reply(NOT_SYNCED);
                    else if (request_token != link_token) error_reply(STALE_LINK);
                    else if (request_id == 0) error_reply(INVALID_ARGUMENT);
                    else if (cache_valid && request_id < last_id) error_reply(STALE_REQUEST);
                    else if (cache_valid && request_id == last_id) begin
                        if (request_command != last_command || request_length != last_length)
                            error_reply(ID_CONFLICT);
                        else begin work_index <= 0; state <= COMPARE; end
                    end else state <= EXECUTE;
                end
                COMPARE: begin
                    if (request_memory[work_index] != last_memory[work_index]) error_reply(ID_CONFLICT);
                    else if (work_index == request_length-1) begin
                        replay <= 1;
                        frame_index <= 0;
                        send_length <= cache_length;
                        state <= SEND;
                    end else work_index <= work_index + 1'b1;
                end
                EXECUTE: begin
                    response_status <= OK;
                    body_kind <= BODY_NONE;
                    response_length <= 10;
                    cache_new <= 1; replay <= 0;
                    work_index <= 0;
                    state <= COPY_REQUEST;
                    case (request_command)
                        8'h01: if (request_length <= 1022) begin
                            body_kind <= BODY_PING;
                            response_length <= request_length + 2;
                        end else response_status <= BAD_LENGTH;
                        8'h02: if (request_length == 8) begin
                            body_kind <= BODY_INFO; response_length <= 44;
                        end else response_status <= BAD_LENGTH;
                        8'h12: if (request_length == 8) begin
                            body_kind <= BODY_CONFIG; response_length <= 48;
                        end else response_status <= BAD_LENGTH;
                        8'h23: if (request_length == 8) begin
                            body_kind <= BODY_STATUS; response_length <= 40;
                        end else response_status <= BAD_LENGTH;
                        8'h04: if(!ENABLE_DIAGNOSTICS) response_status<=UNKNOWN_COMMAND;
                            else if(request_length!=8) response_status<=BAD_LENGTH;
                            else begin diagnostic_snapshot<=diagnostic_status;
                                body_kind<=BODY_DIAGNOSTIC; response_length<=74; end
                        8'h06: if(!ENABLE_RULE_DIAGNOSTICS) response_status<=UNKNOWN_COMMAND;
                            else if(request_length!=9) response_status<=BAD_LENGTH;
                            else if(!diagnostic_ready) response_status<=BUSY;
                            else if(requested_offset>3) response_status<=INVALID_ARGUMENT;
                            else if(ENABLE_EXTERNAL_DIAGNOSTICS && requested_offset!=0) response_status<=INVALID_ARGUMENT;
                            else begin diagnostic_start<=1; diagnostic_case<=requested_offset[1:0]; end
                        8'h08: if(!ENABLE_EXTERNAL_DIAGNOSTICS) response_status<=UNKNOWN_COMMAND;
                            else if(request_length!=8) response_status<=BAD_LENGTH;
                            else diagnostic_stop<=1;
                        8'h05,8'h07: if(!ENABLE_DIAGNOSTICS || (request_command==8'h07 && !ENABLE_RULE_DIAGNOSTICS)) response_status<=UNKNOWN_COMMAND;
                            else if(request_length!=14) response_status<=BAD_LENGTH;
                            else if(!diagnostic_ready) response_status<=BUSY;
                            else if(ENABLE_EXTERNAL_DIAGNOSTICS && diagnostic_status[15:8]==0) response_status<=BUSY;
                            else if(requested_offset>read_limit || requested_count==0 || requested_count>1004)
                                response_status<=INVALID_ARGUMENT;
                            else begin
                                diagnostic_offset<=requested_offset;
                                diagnostic_count<=requested_count>read_limit-requested_offset ? read_limit-requested_offset : requested_count;
                                response_length<=20+(requested_count>read_limit-requested_offset ? read_limit-requested_offset : requested_count);
                                read_generation<=ENABLE_RULE_DIAGNOSTICS ? diagnostic_generation : 1;
                                reading_events<=request_command==8'h07;
                                body_kind<=BODY_DIAGNOSTIC_SAMPLES;
                            end
                        8'h10,8'h11,8'h20,8'h21,8'h22,8'h30,8'h31,8'h32:
                            response_status <= UNSUPPORTED;
                        default: response_status <= UNKNOWN_COMMAND;
                    endcase
                end
                COPY_REQUEST: begin
                    if (work_index == request_length-1) begin
                        last_command <= request_command;
                        last_length <= request_length;
                        last_id <= request_id;
                        cache_valid <= 1;
                        frame_index <= 0;
                        tx_crc <= 16'hffff;
                        state <= body_kind==BODY_DIAGNOSTIC_SAMPLES ? BUILD_WAIT : BUILD;
                    end else work_index <= work_index + 1'b1;
                end
                BUILD: begin
                    if (frame_index >= 2 && frame_index < 10 + response_length)
                        tx_crc <= crc_byte(tx_crc, build_byte);
                    if (frame_index == 11 + response_length) begin
                        send_length <= 12 + response_length;
                        if (cache_new) cache_length <= 12 + response_length;
                        frame_index <= 0;
                        state <= SEND;
                    end else begin
                        frame_index <= frame_index + 1'b1;
                        if(body_kind==BODY_DIAGNOSTIC_SAMPLES) state<=BUILD_WAIT;
                    end
                end
                BUILD_WAIT: state<=BUILD; // One cycle for the synchronous BRAM read.
                SEND: if (tx_ready) begin
                    if (frame_index == send_length-1) begin
                        frame_index <= 0;
                        state <= MAGIC_T;
                    end else frame_index <= frame_index + 1'b1;
                end
                default: state <= MAGIC_T;
            endcase
        end
    end
endmodule
