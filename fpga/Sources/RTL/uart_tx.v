`timescale 1ns/1ps

// Latch a byte only on valid && ready; hold each 8N1 bit for BIT_CYCLES.
module uart_tx #(
    parameter integer CLOCK_HZ = 50_000_000,
    parameter integer BAUD = 115200
) (
    input wire clk,
    input wire rst_n,
    input wire [7:0] data,
    input wire valid,
    output wire ready,
    output reg serial_out
);
    localparam integer BIT_CYCLES = (CLOCK_HZ + BAUD / 2) / BAUD;
    localparam integer COUNT_WIDTH = $clog2(BIT_CYCLES) < 1 ? 1 : $clog2(BIT_CYCLES);
    localparam [1:0] IDLE = 0, START = 1, DATA = 2, STOP = 3;
    reg [1:0] state;
    reg [COUNT_WIDTH-1:0] timer;
    reg [2:0] bit_index;
    reg [7:0] latched_data;
    assign ready = state == IDLE;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            timer <= 0;
            bit_index <= 0;
            latched_data <= 0;
            serial_out <= 1;
        end else begin
            case (state)
                IDLE: begin
                    serial_out <= 1;
                    timer <= 0;
                    if (valid) begin
                        latched_data <= data;
                        bit_index <= 0;
                        serial_out <= 0;
                        state <= START;
                    end
                end
                START: begin
                    if (timer == BIT_CYCLES - 1) begin
                        timer <= 0;
                        serial_out <= latched_data[0];
                        state <= DATA;
                    end else timer <= timer + 1'b1;
                end
                DATA: begin
                    if (timer == BIT_CYCLES - 1) begin
                        timer <= 0;
                        if (bit_index == 7) begin
                            serial_out <= 1;
                            state <= STOP;
                        end else begin
                            bit_index <= bit_index + 1'b1;
                            serial_out <= latched_data[bit_index + 1'b1];
                        end
                    end else timer <= timer + 1'b1;
                end
                STOP: begin
                    if (timer == BIT_CYCLES - 1) begin
                        timer <= 0;
                        state <= IDLE;
                    end else timer <= timer + 1'b1;
                end
            endcase
        end
    end
endmodule
