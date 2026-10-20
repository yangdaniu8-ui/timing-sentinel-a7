`timescale 1ns/1ps

// 8N1 listener on an ALREADY synchronized line. Timing reference is exactly
// the stop-bit centre validation edge. Runtime period is in clock ticks;
// changing it is only allowed while clear is asserted / monitoring disabled.
module target_uart_monitor (
    input wire clk, input wire rst_n, input wire clear,
    input wire serial_level, input wire [31:0] bit_period,
    output wire byte_valid, output wire frame_error, output wire [7:0] data
);
    localparam IDLE=0, START=1, DATA=2, STOP=3;
    reg [1:0] state;
    reg [31:0] timer, high_ticks;
    reg [2:0] bit_index;
    reg [7:0] shift;
    reg running;
    wire period_ok=bit_period>=4;
    // Do not let asynchronous board-reset assertion directly gate event-BRAM
    // enables. running is a synchronously reset control flop.
    always @(posedge clk) if(!rst_n) running<=0; else running<=1;
    assign byte_valid=running && !clear && state==STOP && timer==0 && serial_level;
    assign frame_error=running && !clear && state==STOP && timer==0 && !serial_level;
    assign data=shift;
    always @(posedge clk) begin
        if (!rst_n || clear || !period_ok) begin
            state<=IDLE; timer<=0; high_ticks<=0; bit_index<=0; shift<=0;
        end else begin
            if(serial_level) begin
                if(high_ticks<bit_period) high_ticks<=high_ticks+1'b1;
            end else high_ticks<=0;
            case(state)
                IDLE: if(!serial_level && high_ticks>=bit_period) begin
                    state<=START; timer<=(bit_period>>1)-1;
                end
                START: if(timer==0) begin
                    if(serial_level) state<=IDLE; // Reject short start glitch.
                    else begin state<=DATA; bit_index<=0; timer<=bit_period-1; end
                end else timer<=timer-1'b1;
                DATA: if(timer==0) begin
                    shift[bit_index]<=serial_level; timer<=bit_period-1;
                    if(bit_index==7) state<=STOP;
                    else bit_index<=bit_index+1'b1;
                end else timer<=timer-1'b1;
                STOP: if(timer==0) state<=IDLE;
                    else timer<=timer-1'b1;
                default: state<=IDLE;
            endcase
        end
    end
endmodule
