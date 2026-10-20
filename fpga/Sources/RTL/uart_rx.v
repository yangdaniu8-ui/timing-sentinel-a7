`timescale 1ns/1ps

// Asynchronous 8N1 receiver, sampled at the centre of each bit.
// A framing error blocks further decoding until the line returns high.
module uart_rx #(
    parameter integer CLOCK_HZ = 50_000_000,
    parameter integer BAUD = 115200
) (
    input wire clk,
    input wire rst_n,
    input wire serial_in,
    output reg [7:0] data,
    output reg valid,
    output reg framing_error
);
    localparam integer BIT_CYCLES = (CLOCK_HZ + BAUD / 2) / BAUD;
    localparam integer COUNT_WIDTH = $clog2(BIT_CYCLES) < 1 ? 1 : $clog2(BIT_CYCLES);
    localparam [2:0] IDLE = 0, START = 1, DATA = 2, STOP = 3, WAIT_HIGH = 4;
    (* ASYNC_REG = "TRUE" *) reg [1:0] rx_sync;
    reg [2:0] state;
    reg [COUNT_WIDTH-1:0] timer;
    reg [2:0] bit_index;
    reg [7:0] shift_data;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) rx_sync <= 2'b11;
        else rx_sync <= {rx_sync[0], serial_in};
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            timer <= 0;
            bit_index <= 0;
            shift_data <= 0;
            data <= 0;
            valid <= 0;
            framing_error <= 0;
        end else begin
            valid <= 0;
            framing_error <= 0;
            case (state)
                IDLE: begin
                    timer <= 0;
                    if (!rx_sync[1]) state <= START;
                end
                START: begin
                    if (timer == BIT_CYCLES / 2 - 1) begin
                        timer <= 0;
                        bit_index <= 0;
                        state <= rx_sync[1] ? IDLE : DATA;
                    end else timer <= timer + 1'b1;
                end
                DATA: begin
                    if (timer == BIT_CYCLES - 1) begin
                        timer <= 0;
                        shift_data[bit_index] <= rx_sync[1];
                        if (bit_index == 7) state <= STOP;
                        else bit_index <= bit_index + 1'b1;
                    end else timer <= timer + 1'b1;
                end
                STOP: begin
                    if (timer == BIT_CYCLES - 1) begin
                        timer <= 0;
                        if (rx_sync[1]) begin
                            data <= shift_data;
                            valid <= 1;
                            state <= IDLE;
                        end else begin
                            framing_error <= 1;
                            state <= WAIT_HIGH;
                        end
                    end else timer <= timer + 1'b1;
                end
                WAIT_HIGH: begin
                    timer <= 0;
                    if (rx_sync[1]) state <= IDLE;
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
