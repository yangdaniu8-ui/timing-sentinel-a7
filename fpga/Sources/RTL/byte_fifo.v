`timescale 1ns/1ps

// Small synchronous FIFO with asynchronous read and no RAM-array reset.
// A simultaneous pop permits a push even when the FIFO starts full.
module byte_fifo #(
    parameter integer DEPTH = 256
) (
    input wire clk,
    input wire rst_n,
    input wire push_valid,
    input wire [7:0] push_data,
    output wire push_ready,
    output wire pop_valid,
    output wire [7:0] pop_data,
    input wire pop_ready
);
    localparam integer PTR_WIDTH = $clog2(DEPTH) < 1 ? 1 : $clog2(DEPTH);
    (* ram_style = "distributed" *) reg [7:0] memory [0:DEPTH-1];
    reg [PTR_WIDTH-1:0] write_ptr, read_ptr;
    reg [PTR_WIDTH:0] count;
    wire pop = pop_valid && pop_ready;
    wire push = push_valid && push_ready;
    assign pop_valid = count != 0;
    assign pop_data = memory[read_ptr];
    assign push_ready = (count < DEPTH) || pop;

    // Keep RAM writes out of the asynchronous-reset process. Only metadata
    // needs reset; resetting/gating the array via an async process prevents
    // Vivado from inferring distributed RAM.
    always @(posedge clk) begin
        if (rst_n && push) memory[write_ptr] <= push_data;
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            write_ptr <= 0;
            read_ptr <= 0;
            count <= 0;
        end else begin
            if (push) begin
                write_ptr <= write_ptr == DEPTH - 1 ? 0 : write_ptr + 1'b1;
            end
            if (pop) read_ptr <= read_ptr == DEPTH - 1 ? 0 : read_ptr + 1'b1;
            case ({push, pop})
                2'b10: count <= count + 1'b1;
                2'b01: count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end
endmodule
