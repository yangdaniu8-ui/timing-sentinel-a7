`timescale 1ns/1ps

// 1024 x 128-bit circular event store (4 RAMB36 in 7-series). Clear metadata,
// never RAM. Reads use chronological word index; only frozen records exposed.
module event_buffer #(
    parameter integer ADDR_BITS=10, parameter integer DEPTH=1<<ADDR_BITS
) (
    input wire clk, input wire rst_n, input wire clear,
    input wire write_enable, input wire [127:0] event_data,
    output reg [15:0] count, output reg [31:0] overwrites,
    input wire frozen, input wire read_enable,
    input wire [ADDR_BITS-1:0] read_address,
    output reg [127:0] read_data, output reg read_valid
);
    (* ram_style="block" *) reg [127:0] memory[0:DEPTH-1];
    reg [ADDR_BITS-1:0] pointer;
    wire [ADDR_BITS-1:0] physical_address=(count==DEPTH ? pointer : 0)+read_address;
    always @(posedge clk) begin
        if(write_enable && !frozen && !clear) memory[pointer]<=event_data;
        if(read_enable && frozen && read_address<count) read_data<=memory[physical_address];
    end
    always @(posedge clk) begin
        if(!rst_n || clear) begin count<=0; overwrites<=0; pointer<=0; read_valid<=0; end
        else begin
            read_valid<=read_enable && frozen && read_address<count;
            if(write_enable && !frozen) begin
                pointer<=pointer+1'b1;
                if(count<DEPTH) count<=count+1'b1;
                else if(overwrites!=32'hffffffff) overwrites<=overwrites+1'b1;
            end
        end
    end
endmodule
