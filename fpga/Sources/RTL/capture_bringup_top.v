`timescale 1ns/1ps

// Internal capture self-test + honest profile-0 communication. No external GPIO.
module capture_bringup_top (
    input wire sys_clk, input wire sys_rst_n, input wire uart_rxd,
    output wire uart_txd, output wire [3:0] led
);
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_release;
    always @(posedge sys_clk or negedge sys_rst_n)
        if(!sys_rst_n) reset_release<=0;
        else reset_release<={reset_release[0],1'b1};
    wire rst_n=reset_release[1];
    wire [7:0] received_data,queued_data,transmit_data,diagnostic_data;
    wire received_valid,framing_error,queue_ready,queue_valid,consume;
    wire transmit_valid,transmit_ready,synced,protocol_error,diagnostic_ready,passed,failed;
    wire [15:0] diagnostic_address;
    wire [511:0] diagnostic_status;
    reg [24:0] heartbeat_count;
    reg heartbeat,error_latched;
    uart_rx receiver(.clk(sys_clk),.rst_n(rst_n),.serial_in(uart_rxd),
        .data(received_data),.valid(received_valid),.framing_error(framing_error));
    byte_fifo #(.DEPTH(2048)) input_queue(.clk(sys_clk),.rst_n(rst_n),
        .push_valid(received_valid),.push_data(received_data),.push_ready(queue_ready),
        .pop_valid(queue_valid),.pop_data(queued_data),.pop_ready(consume));
    capture_selftest selftest(.clk(sys_clk),.rst_n(rst_n),
        .diagnostic_address(diagnostic_address),.diagnostic_data(diagnostic_data),
        .diagnostic_status(diagnostic_status),.diagnostic_ready(diagnostic_ready),
        .passed(passed),.failed(failed));
    protocol_link #(.ENABLE_DIAGNOSTICS(1),
        .FIRMWARE_ID(128'h000000302e322e302d37612d6b6e696c)) link (
        .clk(sys_clk),.rst_n(rst_n),.rx_data(queued_data),.rx_valid(queue_valid),.rx_ready(consume),
        .tx_data(transmit_data),.tx_valid(transmit_valid),.tx_ready(transmit_ready),.synced(synced),
        .protocol_error(protocol_error),.diagnostic_status(diagnostic_status),
        .diagnostic_ready(diagnostic_ready),.diagnostic_address(diagnostic_address),.diagnostic_data(diagnostic_data));
    uart_tx transmitter(.clk(sys_clk),.rst_n(rst_n),.data(transmit_data),
        .valid(transmit_valid),.ready(transmit_ready),.serial_out(uart_txd));
    assign led={error_latched|failed,passed,synced,heartbeat};
    always @(posedge sys_clk or negedge rst_n) begin
        if(!rst_n) begin heartbeat_count<=0;heartbeat<=0;error_latched<=0;end
        else begin
            if(heartbeat_count==24999999) begin heartbeat_count<=0;heartbeat<=~heartbeat;end
            else heartbeat_count<=heartbeat_count+1'b1;
            if(framing_error || protocol_error || (received_valid && !queue_ready)) error_latched<=1;
        end
    end
endmodule
