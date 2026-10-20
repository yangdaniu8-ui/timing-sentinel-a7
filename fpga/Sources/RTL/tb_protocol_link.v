`timescale 1ns/1ps

module tb_protocol_link #(parameter integer DIAGNOSTIC_TEST=0, parameter integer RULE_DIAGNOSTIC_TEST=0,
    parameter integer EXTERNAL_DIAGNOSTIC_TEST=0);
    reg clk=0;
    always #10 clk=~clk;
    reg rst_n=0, rx_valid=0;
    reg [7:0] rx_data=0;
    wire rx_ready, tx_valid, synced, protocol_error;
    wire [7:0] tx_data;
    reg [3:0] cycles=0;
    wire tx_ready = cycles[2:0] >= 3; // Deterministic output backpressure.
    reg [31:0] vectors [0:65535];
    reg [7:0] received [0:32767];
    reg [4095:0] vector_file;
    integer received_count=0, checked_count=0, error_count=0;
    integer index, waited;
    reg [31:0] operation;
    reg diagnostic_ready=0;
    wire [15:0] diagnostic_address;
    reg [7:0] diagnostic_data=0;
    reg [7:0] event_data=0;
    wire [13:0] event_address;
    wire start,stop;
    wire [1:0] scenario;
    reg [31:0] generation=32'h10203040;
    integer starts=0,stops=0;
    reg [511:0] diagnostic_status=512'h3f3e3d3c3b3a393837363534333231302f2e2d2c2b2a292827262524232221201f1e1d1c1b1a191817161514131211100f0e0d0c0b0a09080706050403020100;
    always @(posedge clk) diagnostic_data<=diagnostic_address[7:0]^diagnostic_address[15:8]^8'ha7;
    always @(posedge clk) begin
        event_data<=event_address[7:0]^event_address[13:8]^8'h5a;
        if(rst_n && start) begin generation<=generation+1;starts=starts+1;end
        if(rst_n && stop) stops=stops+1;
    end
    protocol_link #(.FRAME_TIMEOUT_CYCLES(20000),.ENABLE_DIAGNOSTICS(DIAGNOSTIC_TEST),
        .ENABLE_RULE_DIAGNOSTICS(RULE_DIAGNOSTIC_TEST),
        .ENABLE_EXTERNAL_DIAGNOSTICS(EXTERNAL_DIAGNOSTIC_TEST),
        .FIRMWARE_ID(EXTERNAL_DIAGNOSTIC_TEST ? 128'h000000312e302e302d37612d747865 : RULE_DIAGNOSTIC_TEST ? 128'h000000302e332e302d37612d6b6e696c :
            DIAGNOSTIC_TEST ? 128'h000000302e322e302d37612d6b6e696c : 128'h000000302e312e302d37612d6b6e696c)) dut (
        .clk(clk), .rst_n(rst_n), .rx_data(rx_data), .rx_valid(rx_valid),
        .rx_ready(rx_ready), .tx_data(tx_data), .tx_valid(tx_valid),
        .tx_ready(tx_ready), .synced(synced), .protocol_error(protocol_error),
        .diagnostic_status(diagnostic_status),.diagnostic_ready(diagnostic_ready),
        .diagnostic_address(diagnostic_address),.diagnostic_data(diagnostic_data),
        .diagnostic_generation(generation),.diagnostic_start(start),.diagnostic_case(scenario),.diagnostic_stop(stop),
        .diagnostic_event_bytes(32'd592),.diagnostic_event_address(event_address),.diagnostic_event_data(event_data));
    always @(posedge clk) begin
        cycles <= cycles+1'b1;
        if (rst_n && tx_valid && tx_ready) begin
            received[received_count] = tx_data;
            received_count = received_count+1;
        end
        if (protocol_error) error_count = error_count+1;
    end
    initial begin
        if(EXTERNAL_DIAGNOSTIC_TEST) begin
            diagnostic_status[7:0]=3;diagnostic_status[15:8]=0;diagnostic_status[95:64]=65536;
        end
        if (!$value$plusargs("VECTORS=%s", vector_file)) $fatal(1,"Missing VECTORS plusarg");
        $readmemh(vector_file, vectors);
        #200;
        @(negedge clk); rst_n=1;
        index=0;
        while (vectors[index] !== 32'hff000000) begin
            operation = vectors[index];
            case (operation[31:24])
                1: begin
                    @(negedge clk); rx_data=operation[7:0]; rx_valid=1;
                    waited=0;
                    while (!rx_ready && waited<50000) begin
                        @(negedge clk); waited=waited+1;
                    end
                    if (!rx_ready) $fatal(1,"RX ready timeout vector=%0d",index);
                    @(posedge clk); #1;
                    @(negedge clk); rx_valid=0;
                end
                2: begin
                    waited=0;
                    while (received_count <= checked_count && waited<50000) begin
                        @(negedge clk); waited=waited+1;
                    end
                    if (received_count <= checked_count) $fatal(1,"Missing reply byte vector=%0d",index);
                    if (received[checked_count] !== operation[7:0])
                        $fatal(1,"Reply mismatch vector=%0d byte=%0d got=%02x expected=%02x",index,checked_count,received[checked_count],operation[7:0]);
                    checked_count=checked_count+1;
                end
                3: begin
                    repeat (operation[23:0]) @(negedge clk);
                    if (received_count != checked_count) $fatal(1,"Bad frame produced a reply");
                end
                4: diagnostic_ready=operation[0];
                5: diagnostic_status[15:8]=operation[7:0];
                6: diagnostic_status[95:64]=operation[23:0];
                default: $fatal(1,"Unknown vector opcode %08x",operation);
            endcase
            index=index+1;
            if (index>=65535) $fatal(1,"Vector EOF missing");
        end
        repeat(1000) @(negedge clk);
        if (received_count != checked_count || !synced || error_count<4)
            $fatal(1,"Final protocol state mismatch");
        @(negedge clk); rst_n=0;
        #100;
        if (synced || tx_valid) $fatal(1,"Reset did not clear link/output state");
        if(EXTERNAL_DIAGNOSTIC_TEST) begin
            if(starts!=2 || stops!=1) $fatal(1,"External duplicate side effect STARTS=%d STOPS=%d",starts,stops);
            $display("PROTOCOL_EXTERNAL_DIAGNOSTICS_TB_PASS bytes=%0d STARTS=%0d STOPS=%0d",checked_count,starts,stops);
        end else if(RULE_DIAGNOSTIC_TEST) begin
            if(starts!=2) $fatal(1,"Cached side effect executed more than once: %0d",starts);
            $display("PROTOCOL_RULE_DIAGNOSTICS_TB_PASS bytes=%0d STARTS=%0d",checked_count,starts);
        end else if(DIAGNOSTIC_TEST) $display("PROTOCOL_DIAGNOSTICS_TB_PASS bytes=%0d bram_latency=PASS replay=PASS boundaries=PASS",checked_count);
        else $display("PROTOCOL_LINK_TB_PASS bytes=%0d crc_collision=PASS cache=PASS token=PASS timeout=PASS backpressure=PASS reset=PASS",checked_count);
        $finish;
    end
    initial begin
        #100_000_000;
        $fatal(1,"Protocol testbench watchdog timeout");
    end
endmodule

module tb_protocol_diagnostics;
    tb_protocol_link #(.DIAGNOSTIC_TEST(1)) bench();
endmodule

module tb_protocol_rule_diagnostics;
    tb_protocol_link #(.DIAGNOSTIC_TEST(1),.RULE_DIAGNOSTIC_TEST(1)) bench();
endmodule

module tb_protocol_external_diagnostics;
    tb_protocol_link #(.DIAGNOSTIC_TEST(1),.RULE_DIAGNOSTIC_TEST(1),.EXTERNAL_DIAGNOSTIC_TEST(1)) bench();
endmodule
