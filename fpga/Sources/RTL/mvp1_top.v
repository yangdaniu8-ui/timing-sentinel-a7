`timescale 1ns/1ps
module mvp1_top(
    input wire sys_clk,input wire sys_rst_n,input wire uart_rxd,output wire uart_txd,
    output wire [3:0] led,input wire [7:0] probe_inputs,output wire target_ready
);
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_release;
    always @(posedge sys_clk or negedge sys_rst_n)
        if(!sys_rst_n) reset_release<=0;else reset_release<={reset_release[0],1'b1};
    wire rst_n=reset_release[1];
    wire [7:0] rx_data,queue_data,tx_data,command,stream_data;
    wire rx_valid,frame_error,queue_ready,queue_valid,consume,tx_valid,tx_ready;
    wire synced,protocol_error,command_valid,command_done,stream_response;
    wire [15:0] argument_length,command_status;wire [10:0] body_length,stream_index;
    wire [271:0] arguments;wire [1055:0] snapshot;
    wire [2:0] capture_state;wire [31:0] session_id,config_id;
    reg [24:0] heartbeat_count;reg heartbeat,error_latched;
    uart_rx rx(.clk(sys_clk),.rst_n(rst_n),.serial_in(uart_rxd),.data(rx_data),.valid(rx_valid),.framing_error(frame_error));
    byte_fifo #(.DEPTH(2048)) queue(.clk(sys_clk),.rst_n(rst_n),.push_valid(rx_valid),
        .push_data(rx_data),.push_ready(queue_ready),.pop_valid(queue_valid),.pop_data(queue_data),.pop_ready(consume));
    mvp1_protocol link(.clk(sys_clk),.rst_n(rst_n),.rx_data(queue_data),.rx_valid(queue_valid),.rx_ready(consume),
        .tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready),.synced(synced),.protocol_error(protocol_error),
        .command_valid(command_valid),.command(command),.argument_length(argument_length),.arguments(arguments),
        .command_done(command_done),.command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data));
    mvp1_capture capture(.clk(sys_clk),.rst_n(rst_n),.raw_inputs(probe_inputs),.target_ready(target_ready),
        .command_valid(command_valid),.command(command),.argument_length(argument_length),.arguments(arguments),
        .command_done(command_done),.command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data),
        .state(capture_state),.session_id(session_id),.config_id(config_id));
    uart_tx tx(.clk(sys_clk),.rst_n(rst_n),.data(tx_data),.valid(tx_valid),.ready(tx_ready),.serial_out(uart_txd));
    assign led={error_latched,capture_state==4,synced,heartbeat};
    always @(posedge sys_clk) begin
        if(!rst_n) begin heartbeat_count<=0;heartbeat<=0;error_latched<=0;end
        else begin
            if(heartbeat_count==24999999) begin heartbeat_count<=0;heartbeat<=~heartbeat;end
            else heartbeat_count<=heartbeat_count+1'b1;
            if(frame_error || protocol_error || (rx_valid && !queue_ready)) error_latched<=1;
        end
    end
endmodule
