`timescale 1ns/1ps
module external_rule_top (
    input wire sys_clk, input wire sys_rst_n, input wire uart_rxd,
    output wire uart_txd, output wire [3:0] led,
    input wire target_uart, input wire target_gpio, output wire target_ready
);
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_release;
    always @(posedge sys_clk or negedge sys_rst_n)
        if(!sys_rst_n) reset_release<=0;
        else reset_release<={reset_release[0],1'b1};
    wire rst_n=reset_release[1];
    wire [7:0] received_data,queued_data,transmit_data,diagnostic_data,event_data;
    wire received_valid,framing_error,queue_ready,queue_valid,consume;
    wire transmit_valid,transmit_ready,synced,protocol_error,diagnostic_ready,frozen,failed,start,stop;
    wire [15:0] diagnostic_address;
    wire [13:0] event_address;
    wire [1:0] unused_scenario;
    wire [31:0] generation,event_bytes;
    wire [511:0] diagnostic_status;
    reg [24:0] heartbeat_count;
    reg heartbeat,error_latched;
    uart_rx receiver(.clk(sys_clk),.rst_n(rst_n),.serial_in(uart_rxd),
        .data(received_data),.valid(received_valid),.framing_error(framing_error));
    byte_fifo #(.DEPTH(2048)) input_queue(.clk(sys_clk),.rst_n(rst_n),
        .push_valid(received_valid),.push_data(received_data),.push_ready(queue_ready),
        .pop_valid(queue_valid),.pop_data(queued_data),.pop_ready(consume));
    external_rule_capture capture(.clk(sys_clk),.rst_n(rst_n),
        .target_uart(target_uart),.target_gpio(target_gpio),.target_ready(target_ready),
        .diagnostic_start(start),.diagnostic_stop(stop),
        .diagnostic_address(diagnostic_address),.diagnostic_data(diagnostic_data),
        .event_byte_address(event_address),.event_byte_data(event_data),
        .event_byte_count(event_bytes),.generation(generation),
        .diagnostic_status(diagnostic_status),.diagnostic_ready(diagnostic_ready),.frozen(frozen),.failed(failed));
    protocol_link #(.ENABLE_DIAGNOSTICS(1),.ENABLE_RULE_DIAGNOSTICS(1),.ENABLE_EXTERNAL_DIAGNOSTICS(1),
        .FIRMWARE_ID(128'h000000312e302e302d37612d747865)) link (
        .clk(sys_clk),.rst_n(rst_n),.rx_data(queued_data),.rx_valid(queue_valid),.rx_ready(consume),
        .tx_data(transmit_data),.tx_valid(transmit_valid),.tx_ready(transmit_ready),.synced(synced),
        .protocol_error(protocol_error),.diagnostic_status(diagnostic_status),
        .diagnostic_ready(diagnostic_ready),.diagnostic_address(diagnostic_address),.diagnostic_data(diagnostic_data),
        .diagnostic_generation(generation),.diagnostic_start(start),.diagnostic_stop(stop),.diagnostic_case(unused_scenario),
        .diagnostic_event_bytes(event_bytes),.diagnostic_event_address(event_address),.diagnostic_event_data(event_data));
    uart_tx transmitter(.clk(sys_clk),.rst_n(rst_n),.data(transmit_data),
        .valid(transmit_valid),.ready(transmit_ready),.serial_out(uart_txd));
    assign led={error_latched|failed,frozen,synced,heartbeat};
    always @(posedge sys_clk) begin
        if(!rst_n) begin heartbeat_count<=0;heartbeat<=0;error_latched<=0;end
        else begin
            if(heartbeat_count==24999999) begin heartbeat_count<=0;heartbeat<=~heartbeat;end
            else heartbeat_count<=heartbeat_count+1'b1;
            if(framing_error || protocol_error || (received_valid && !queue_ready)) error_latched<=1;
        end
    end
endmodule
