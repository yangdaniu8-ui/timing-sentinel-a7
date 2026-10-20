`timescale 1ns/1ps

// Stage-1 communication test; this firmware is not the MVP-1 command protocol.
module uart_echo_top #(
    parameter integer CLOCK_HZ = 50_000_000,
    parameter integer BAUD = 115200,
    parameter integer FIFO_DEPTH = 256
) (
    input wire sys_clk,
    input wire sys_rst_n,
    input wire uart_rxd,
    output wire uart_txd,
    output wire [3:0] led
);
    (* ASYNC_REG = "TRUE" *) reg [1:0] reset_release;
    always @(posedge sys_clk or negedge sys_rst_n) begin
        if (!sys_rst_n) reset_release <= 0;
        else reset_release <= {reset_release[0], 1'b1};
    end
    wire rst_n = reset_release[1];
    wire [7:0] received_data, queued_data;
    wire received_valid, framing_error, queue_ready, queue_valid, tx_ready;
    reg rx_activity, tx_activity, error_latched, heartbeat;
    localparam integer HEARTBEAT_WIDTH = $clog2(CLOCK_HZ / 2);
    reg [HEARTBEAT_WIDTH-1:0] heartbeat_count;

    uart_rx #(.CLOCK_HZ(CLOCK_HZ), .BAUD(BAUD)) receiver (
        .clk(sys_clk), .rst_n(rst_n), .serial_in(uart_rxd),
        .data(received_data), .valid(received_valid), .framing_error(framing_error)
    );
    byte_fifo #(.DEPTH(FIFO_DEPTH)) echo_queue (
        .clk(sys_clk), .rst_n(rst_n), .push_valid(received_valid),
        .push_data(received_data), .push_ready(queue_ready),
        .pop_valid(queue_valid), .pop_data(queued_data), .pop_ready(tx_ready)
    );
    uart_tx #(.CLOCK_HZ(CLOCK_HZ), .BAUD(BAUD)) transmitter (
        .clk(sys_clk), .rst_n(rst_n), .data(queued_data),
        .valid(queue_valid), .ready(tx_ready), .serial_out(uart_txd)
    );
    assign led = {error_latched, tx_activity, rx_activity, heartbeat};

    always @(posedge sys_clk or negedge rst_n) begin
        if (!rst_n) begin
            heartbeat_count <= 0;
            heartbeat <= 0;
            rx_activity <= 0;
            tx_activity <= 0;
            error_latched <= 0;
        end else begin
            if (heartbeat_count == CLOCK_HZ / 2 - 1) begin
                heartbeat_count <= 0;
                heartbeat <= ~heartbeat;
            end else heartbeat_count <= heartbeat_count + 1'b1;
            if (received_valid) rx_activity <= ~rx_activity;
            if (queue_valid && tx_ready) tx_activity <= ~tx_activity;
            if (framing_error || (received_valid && !queue_ready)) error_latched <= 1;
        end
    end
endmodule
