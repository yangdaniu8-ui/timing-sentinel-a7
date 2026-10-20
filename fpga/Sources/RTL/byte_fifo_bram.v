`timescale 1ns/1ps

// Same first-word-visible interface/capacity as byte_fifo. Store every accepted
// byte in synchronous BRAM, with a head register and one-word lookahead.
// Head/lookahead duplicates are not extra logical queue capacity.
module byte_fifo_bram #(parameter integer DEPTH=2048)(
    input wire clk,input wire rst_n,
    input wire push_valid,input wire [7:0] push_data,output wire push_ready,
    output wire pop_valid,output wire [7:0] pop_data,input wire pop_ready
);
    localparam integer PTR_WIDTH=$clog2(DEPTH)<1 ? 1 : $clog2(DEPTH);
    (* ram_style="block" *) reg [7:0] memory[0:DEPTH-1];
    reg [PTR_WIDTH-1:0] write_ptr,read_ptr;
    reg [PTR_WIDTH:0] count;
    reg [7:0] head;
    assign pop_valid=count!=0;
    assign pop_data=head;
    wire pop=pop_valid && pop_ready;
    assign push_ready=count<DEPTH || pop;
    wire push=push_valid && push_ready;
    function [PTR_WIDTH-1:0] advance;
        input [PTR_WIDTH-1:0] pointer;
        begin advance=pointer==DEPTH-1 ? 0 : pointer+1'b1;end
    endfunction
    wire [PTR_WIDTH:0] next_count=push && !pop ? count+1'b1 :
        pop && !push ? count-1'b1 : count;

    // These mirrors are SYNCHRONOUSLY reset flops. Async-reset metadata and
    // producer valid/data never directly drive BRAM control/address pins.
    // Capture the actual public post-edge state, including after a short
    // asynchronous reset pulse between clocks; don't increment stale mirrors.
    reg [PTR_WIDTH-1:0] read_sync;
    reg [PTR_WIDTH:0] count_sync;
    wire predict_pop=count_sync!=0 && pop_ready;
    wire [PTR_WIDTH-1:0] prefetch_address=predict_pop ? advance(advance(read_sync)) : advance(read_sync);
    reg write_pending;
    reg [PTR_WIDTH-1:0] pending_address;
    reg [7:0] pending_data,ram_q,bypass_data;
    reg bypass_valid;
    wire [7:0] lookahead=bypass_valid ? bypass_data : ram_q;

    always @(posedge clk) begin
        write_pending<=rst_n && push;
        pending_address<=write_ptr;pending_data<=push_data;
        if(!rst_n) begin read_sync<=0;count_sync<=0;end
        else begin read_sync<=pop ? advance(read_ptr) : read_ptr;count_sync<=next_count;end
        // Array write is one clock late. Forward BOTH a newly accepted write
        // and the pending write; newest accepted write has priority. Forwarding
        // also handles full-ring collisions and DEPTH1/2 aliasing.
        bypass_valid<=rst_n && ((push && write_ptr==prefetch_address) ||
            (write_pending && pending_address==prefetch_address));
        bypass_data<=push && write_ptr==prefetch_address ? push_data : pending_data;
    end
    // No RAM/read-register reset; pending writes at reset are invalidated by
    // logical metadata. Read address and write controls have synchronous Qs.
    always @(posedge clk) begin
        if(write_pending) memory[pending_address]<=pending_data;
        ram_q<=memory[prefetch_address];
    end
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin write_ptr<=0;read_ptr<=0;count<=0;head<=0;end
        else begin
            if(push) write_ptr<=advance(write_ptr);
            if(pop) read_ptr<=advance(read_ptr);
            count<=next_count;
            if(pop) begin
                if(count==1) begin if(push) head<=push_data;end
                else head<=lookahead;
            end else if(count==0 && push) head<=push_data;
        end
    end
endmodule
