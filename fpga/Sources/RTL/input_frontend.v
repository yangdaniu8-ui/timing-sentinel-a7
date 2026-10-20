`timescale 1ns/1ps

// One shared sampling clock. Edge flags refer to the CURRENT synchronized
// level and are consumed at this clock edge, not one pipeline stage later.
module input_frontend (
    input wire clk, input wire rst_n, input wire clear_history,
    input wire [7:0] raw, output wire [7:0] levels,
    output wire [7:0] rising, output wire [7:0] falling
);
    (* ASYNC_REG="TRUE" *) reg [7:0] sync_meta, sync_level;
    reg [7:0] previous;
    always @(posedge clk) begin
        if (!rst_n) begin sync_meta<=8'h01; sync_level<=8'h01; previous<=8'h01; end
        else begin
            sync_meta<=raw; sync_level<=sync_meta;
            previous<=sync_level;
        end
    end
    assign levels=sync_level;
    assign rising=clear_history ? 8'd0 : sync_level & ~previous;
    assign falling=clear_history ? 8'd0 : ~sync_level & previous;
endmodule
