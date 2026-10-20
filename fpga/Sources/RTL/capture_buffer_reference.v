`timescale 1ns/1ps

// Single-clock sample-enable ring. Input data/tick must already share clk.
// No asynchronous GPIO frontend here; no target-UART or rule engine yet.
(* use_dsp="no" *) module capture_buffer_reference #(
    parameter integer ADDR_BITS = 16,
    parameter integer DEPTH = 1 << ADDR_BITS
) (
    input wire clk, input wire rst_n,
    input wire [7:0] sample_data, input wire [47:0] logic_tick,
    input wire arm, input wire stop, input wire release_capture,
    input wire [15:0] sample_divisor,
    input wire [31:0] pre_count, input wire [31:0] post_count,
    input wire trigger,
    output reg [2:0] state, output wire ready, output reg command_error,
    output reg [31:0] valid_count,
    output reg trigger_valid, output reg manual_stop,
    output reg [47:0] first_tick, output reg [47:0] anchor_tick,
    output reg [47:0] decision_tick, output reg [31:0] trigger_index,
    input wire read_enable, input wire [ADDR_BITS-1:0] read_address,
    output wire [7:0] read_data, output reg read_valid
);
    localparam IDLE=0, PREFILL=1, ARMED=2, POST_TRIGGER=3, FROZEN=4;
    (* ram_style="block" *) reg [7:0] memory [0:DEPTH-1];
    reg [7:0] memory_q;
    reg [ADDR_BITS-1:0] write_pointer, oldest_pointer;
    reg [15:0] divisor, phase;
    reg [31:0] pre_samples, post_samples, remaining;
    reg pending_trigger;
    reg [47:0] last_sample_tick, pending_decision;
    wire active = state == PREFILL || state == ARMED || state == POST_TRIGGER;
    wire sample_enable = active && phase == 0 && !stop;
    wire [31:0] added_count = valid_count < DEPTH ? valid_count + 1 : DEPTH;
    wire [ADDR_BITS-1:0] physical_read_address = oldest_pointer + read_address;
    wire config_ok = (sample_divisor == 1 || sample_divisor == 5 ||
        sample_divisor == 10 || sample_divisor == 50) &&
        pre_count < DEPTH && post_count >= 1 && post_count < DEPTH &&
        {1'b0,pre_count} + {1'b0,post_count} + 1 == DEPTH;
    assign ready = state == ARMED;
    assign read_data = read_valid ? memory_q : 8'd0;
    function [47:0] span;
        input [31:0] count;
        input [15:0] div;
        reg [47:0] n;
        begin
            n={16'd0,count};
            case(div) 1:span=n; 5:span=(n<<2)+n;
                10:span=(n<<3)+(n<<1); 50:span=(n<<5)+(n<<4)+(n<<1);
                default:span=0; endcase
        end
    endfunction

    // Synchronous simple-dual-port BRAM. Reset metadata, NEVER the array.
    always @(posedge clk) begin
        if (sample_enable) memory[write_pointer] <= sample_data;
        if (state == FROZEN && read_enable && read_address < valid_count)
            memory_q <= memory[physical_read_address];
    end
    // Synchronous control reset keeps BRAM address/control pins free of
    // asynchronously changing registers (Vivado REQP-1839). A reset-edge write,
    // if any, is invalidated by metadata and cannot enter a subsequent record.
    always @(posedge clk) begin
        if (!rst_n) begin
            state <= IDLE; command_error <= 0; read_valid <= 0;
            write_pointer <= 0; oldest_pointer <= 0;
            divisor <= 1; phase <= 0; pre_samples <= 0; post_samples <= 0;
            remaining <= 0; pending_trigger <= 0; pending_decision <= 0;
            valid_count <= 0; trigger_valid <= 0; manual_stop <= 0;
            first_tick <= 0; anchor_tick <= 0; decision_tick <= 0;
            trigger_index <= 32'hffffffff; last_sample_tick <= 0;
        end else begin
            command_error <= (arm && (state != IDLE || !config_ok)) ||
                (release_capture && state != FROZEN) || (stop && !active);
            read_valid <= state == FROZEN && read_enable && read_address < valid_count;
            if (release_capture && state == FROZEN) begin
                state <= IDLE; valid_count <= 0; trigger_valid <= 0;
                manual_stop <= 0; first_tick <= 0; anchor_tick <= 0;
                decision_tick <= 0; trigger_index <= 32'hffffffff;
                read_valid <= 0;
            end else if (arm && state == IDLE && config_ok) begin
                state <= pre_count == 0 ? ARMED : PREFILL;
                divisor <= sample_divisor; phase <= sample_divisor - 1;
                pre_samples <= pre_count; post_samples <= post_count;
                write_pointer <= 0; oldest_pointer <= 0;
                valid_count <= 0; pending_trigger <= 0; pending_decision <= 0;
                trigger_valid <= 0; manual_stop <= 0;
                first_tick <= 0; anchor_tick <= 0; decision_tick <= 0;
                trigger_index <= 32'hffffffff; last_sample_tick <= 0;
            end else if (active && stop) begin
                // Stop wins over same-cycle trigger/sample; unwritten data stay invalid.
                state <= FROZEN; manual_stop <= 1; pending_trigger <= 0;
                oldest_pointer <= write_pointer - valid_count;
                first_tick <= valid_count == 0 ? 0 :
                    last_sample_tick - span(valid_count - 1, divisor);
            end else if (active) begin
                if (phase == 0) phase <= divisor - 1;
                else phase <= phase - 1'b1;
                if (state == ARMED && trigger && !pending_trigger) begin
                    pending_trigger <= 1; pending_decision <= logic_tick;
                end
                if (sample_enable) begin
                    write_pointer <= write_pointer + 1'b1;
                    last_sample_tick <= logic_tick;
                    valid_count <= added_count;
                    if (state == PREFILL && added_count >= pre_samples) state <= ARMED;
                    if (state == ARMED && (pending_trigger || trigger)) begin
                        state <= POST_TRIGGER; remaining <= post_samples;
                        pending_trigger <= 0; trigger_valid <= 1;
                        decision_tick <= pending_trigger ? pending_decision : logic_tick;
                        anchor_tick <= logic_tick; trigger_index <= pre_samples;
                        // Trim earlier history to exactly the requested pre-window.
                        valid_count <= pre_samples + 1;
                    end else if (state == POST_TRIGGER) begin
                        remaining <= remaining - 1'b1;
                        if (remaining == 1) begin
                            state <= FROZEN;
                            oldest_pointer <= write_pointer + 1'b1 - added_count;
                            first_tick <= logic_tick - span(added_count - 1, divisor);
                        end
                    end
                end
            end
        end
    end
endmodule
