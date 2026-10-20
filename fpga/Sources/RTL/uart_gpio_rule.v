`timescale 1ns/1ps

// One pending transaction. First B terminates, including EARLY. Closed
// interval; TIMEOUT at max+1. Busy-at-start A is dropped independently of B.
// Result/accept/overlap are COMBINATIONAL events consumed on logic_tick.
module uart_gpio_rule (
    input wire clk, input wire rst_n, input wire enable, input wire cancel,
    input wire [47:0] logic_tick,
    input wire uart_valid, input wire [7:0] uart_data,
    input wire [7:0] byte_value, input wire [7:0] byte_mask,
    input wire gpio_edge, input wire [47:0] min_ticks, input wire [47:0] max_ticks,
    output reg busy, output wire accepted, output wire overlap,
    output reg result_valid, output reg [3:0] result,
    output wire [47:0] result_a, output wire [47:0] result_b,
    output wire matched_b
);
    reg [47:0] pending_a;
    reg running;
    always @(posedge clk) if(!rst_n) running<=0; else running<=1;
    wire match=uart_valid && (uart_data & byte_mask)==byte_value;
    assign accepted=running && enable && !cancel && !busy && match;
    assign overlap=running && enable && !cancel && busy && match;
    assign result_a=busy ? pending_a : logic_tick;
    wire [47:0] delta=logic_tick-result_a;
    assign matched_b=result_valid && (result==1 || result==2);
    assign result_b=matched_b ? logic_tick : 48'd0;
    always @* begin
        result_valid=0; result=0;
        if(running && enable && !cancel && (busy || accepted)) begin
            if(gpio_edge && delta<=max_ticks) begin
                result_valid=1; result=delta<min_ticks ? 4'd2 : 4'd1;
            end else if(busy && delta>max_ticks) begin
                result_valid=1; result=3;
            end
        end
    end
    always @(posedge clk) begin
        if(!rst_n || cancel || !enable) begin busy<=0; pending_a<=0; end
        else if(result_valid) busy<=0;
        else if(accepted) begin busy<=1; pending_a<=logic_tick; end
    end
endmodule
