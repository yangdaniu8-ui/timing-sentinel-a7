`timescale 1ns/1ps
module tb_mvp5_protocol;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,rx_valid=0;reg [7:0] rx_data=0;
    wire rx_ready,tx_valid,synced,protocol_error;wire [7:0] tx_data;
    reg [9:0] cycles=0;wire tx_ready=(cycles[9:8]!=0 && cycles[2:0]>=3);
    reg holding=0;reg [3295:0] held_body;integer held_bytes=0,h;
    wire command_valid,command_done,stream_response;wire [7:0] command,stream_data;
    wire [15:0] argument_length,command_status;wire [10:0] body_length,stream_index;
    wire [799:0] arguments;wire [3295:0] snapshot;
    wire [2:0] capture_state;wire [31:0] session,config_id;wire target_ready;
    reg [31:0] vectors[0:65535];reg [7:0] received[0:32767];reg [4095:0] vector_file;
    integer received_count=0,checked_count=0,error_count=0,index,waited;
    integer arms=0,stops=0,releases=0,commits=0,packets=0,frame_start,frame_size,j;
    reg [31:0] operation;reg [15:0] packet_crc;
    mvp5_protocol #(.FRAME_TIMEOUT_CYCLES(20000)) link(.clk(clk),.rst_n(rst_n),
        .rx_data(rx_data),.rx_valid(rx_valid),.rx_ready(rx_ready),.tx_data(tx_data),.tx_valid(tx_valid),
        .tx_ready(tx_ready),.synced(synced),.protocol_error(protocol_error),.command_valid(command_valid),
        .command(command),.argument_length(argument_length),.arguments(arguments),.command_done(command_done),
        .command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data));
    mvp5_capture capture(.clk(clk),.rst_n(rst_n),.raw_inputs(8'd1),.target_ready(target_ready),
        .command_valid(command_valid),.command(command),.argument_length(argument_length),.arguments(arguments),
        .command_done(command_done),.command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data),
        .state(capture_state),.session_id(session),.config_id(config_id));
    function [15:0] crc_byte;
        input [15:0] crc;input [7:0] value;reg [15:0] c;integer k;
        begin c=crc^{value,8'd0};for(k=0;k<8;k=k+1) c=c[15] ? (c<<1)^16'h1021 : c<<1;crc_byte=c;end
    endfunction
    always @(posedge clk) begin
        cycles<=cycles+1'b1;
        if(!rst_n || command_valid) holding=0;
        if(rst_n && command_done && link.state==13) begin
            holding=1;held_body=snapshot;held_bytes=stream_response ? 10 : body_length;
        end
        if(holding) begin
            for(h=0;h<held_bytes;h=h+1)
                if(snapshot[h*8+:8]!==held_body[h*8+:8])
                    $fatal(1,"backend response changed before next command byte=%0d",h);
        end
        if(rst_n && tx_valid && tx_ready) begin
            received[received_count]=tx_data;received_count=received_count+1;
            if(received_count>=32767) $fatal(1,"receive bounds");
        end
        if(protocol_error) error_count=error_count+1;
        if(rst_n && capture.arm) arms=arms+1;
        if(rst_n && capture.stop) stops=stops+1;
        if(rst_n && capture.release_capture) releases=releases+1;
        if(rst_n && command_valid && command==8'h11 && capture.command_phase==0 && capture.shadow_valid && capture_state==0) commits=commits+1;
    end
    initial begin
        if(!$value$plusargs("VECTORS=%s",vector_file)) $fatal(1,"Missing VECTORS");
        $readmemh(vector_file,vectors);#200;@(negedge clk);rst_n=1;index=0;
        while(vectors[index]!==32'hff000000) begin
            operation=vectors[index];
            case(operation[31:24])
                1:begin
                    @(negedge clk);rx_data=operation[7:0];rx_valid=1;waited=0;
                    while(!rx_ready && waited<50000) begin @(negedge clk);waited=waited+1;end
                    if(!rx_ready) $fatal(1,"RX timeout %0d",index);
                    @(posedge clk);#1;@(negedge clk);rx_valid=0;
                end
                2,4:begin
                    waited=0;
                    while(received_count<=checked_count && waited<50000) begin @(negedge clk);waited=waited+1;end
                    if(received_count<=checked_count) $fatal(1,"missing response %0d",index);
                    if(operation[31:24]==2 && received[checked_count]!==operation[7:0])
                        $fatal(1,"MVP5_BYTE_FAIL vector=%0d byte=%0d got=%02x expected=%02x",index,checked_count,received[checked_count],operation[7:0]);
                    checked_count=checked_count+1;
                end
                3:begin repeat(operation[23:0]) @(negedge clk);if(received_count!=checked_count) $fatal(1,"unexpected response");end
                5:if(capture.period!=operation[23:0]) $fatal(1,"baud floor division mismatch");
                default:$fatal(1,"bad opcode");
            endcase
            index=index+1;if(index>=65535) $fatal(1,"vector overflow");
        end
        repeat(1000) @(negedge clk);
        if(received_count!=checked_count || !synced || error_count<3 || arms!=1 || stops!=1 || releases!=1 || commits!=7)
            $fatal(1,"MVP5 final state bytes=%d/%d errors=%d ARM=%d STOP=%d RELEASE=%d COMMIT=%d",received_count,checked_count,error_count,arms,stops,releases,commits);
        frame_start=0;
        while(frame_start<received_count) begin
            if(received[frame_start]!=8'h54 || received[frame_start+1]!=8'h53) $fatal(1,"frame boundary");
            frame_size=12+{received[frame_start+9],received[frame_start+8]};packet_crc=16'hffff;
            for(j=frame_start+2;j<frame_start+frame_size-2;j=j+1) packet_crc=crc_byte(packet_crc,received[j]);
            if(packet_crc!={received[frame_start+frame_size-1],received[frame_start+frame_size-2]}) $fatal(1,"response CRC failure");
            packets=packets+1;frame_start=frame_start+frame_size;
        end
        if(frame_start!=received_count || capture_state!=0 || session!=1 || config_id!=8) $fatal(1,"final session/config");
        @(negedge clk);rst_n=0;repeat(5) @(negedge clk);if(synced || tx_valid) $fatal(1,"reset retains link");
        $display("MVP5_PROTOCOL_TB_PASS bytes=%0d packets=%0d ARM=1 STOP=1 RELEASE=1 COMMIT=7",checked_count,packets);$finish;
    end
    initial begin #100000000;$fatal(1,"MVP5_PROTOCOL_GLOBAL_TIMEOUT");end
endmodule


