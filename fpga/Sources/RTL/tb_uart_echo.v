`timescale 1ns/1ps

module tb_uart_echo;
    localparam integer CLOCK_HZ = 50_000_000;
    localparam integer BAUD = 115200;
    localparam integer TX_BIT_NS = 8680;  // 434 FPGA clocks, independent decoder
    localparam integer HOST_BIT_NS = 8681;
    reg clk = 0;
    always #10 clk = ~clk;
    reg rst_n = 0;
    reg rxd = 1;
    wire txd;
    wire [3:0] led;
    reg [7:0] expected [0:4095];
    integer expected_count = 0;
    integer received_count = 0;
    integer rx_pulses = 0;
    integer error_pulses = 0;
    integer i, j, saved_rx, saved_errors;
    reg [31:0] lfsr = 32'h7352026;
    reg [7:0] decoded;

    uart_echo_top dut (
        .sys_clk(clk), .sys_rst_n(rst_n), .uart_rxd(rxd),
        .uart_txd(txd), .led(led)
    );

    always @(posedge clk) begin
        if (dut.received_valid) rx_pulses = rx_pulses + 1;
        if (dut.framing_error) error_pulses = error_pulses + 1;
    end

    // Decode the physical TX output; do not use the DUT's RX to validate itself.
    initial forever begin
        @(negedge txd);
        #(TX_BIT_NS / 2);
        if (txd !== 0) $fatal(1, "Invalid TX start bit");
        for (j = 0; j < 8; j = j + 1) begin
            #(TX_BIT_NS);
            decoded[j] = txd;
        end
        #(TX_BIT_NS);
        if (txd !== 1) $fatal(1, "Invalid TX stop bit");
        if (received_count >= expected_count) $fatal(1, "Unexpected echoed byte %02x", decoded);
        if (decoded !== expected[received_count])
            $fatal(1, "Echo mismatch index=%0d got=%02x expected=%02x", received_count, decoded, expected[received_count]);
        received_count = received_count + 1;
    end

    task send_byte;
        input [7:0] value;
        input integer bit_ns;
        integer k;
        begin
            expected[expected_count] = value;
            expected_count = expected_count + 1;
            rxd = 0;
            #(bit_ns);
            for (k = 0; k < 8; k = k + 1) begin
                rxd = value[k];
                #(bit_ns);
            end
            rxd = 1;
            #(bit_ns);
        end
    endtask

    task wait_for_echoes;
        integer waited;
        begin
            waited = 0;
            while (received_count < expected_count && waited < 200) begin
                #(HOST_BIT_NS * 10);
                waited = waited + 1;
            end
            if (received_count != expected_count)
                $fatal(1, "Missing echoes got=%0d expected=%0d", received_count, expected_count);
            #(HOST_BIT_NS * 2);
        end
    endtask

    // Independent FIFO boundary test: full, rejected push, full pop+push, wrap.
    reg unit_rst_n = 0, unit_push = 0, unit_pop = 0;
    reg [7:0] unit_data = 0;
    wire unit_ready, unit_valid;
    wire [7:0] unit_front;
    reg fifo_done = 0;
    byte_fifo #(.DEPTH(4)) unit_fifo (
        .clk(clk), .rst_n(unit_rst_n), .push_valid(unit_push),
        .push_data(unit_data), .push_ready(unit_ready),
        .pop_valid(unit_valid), .pop_data(unit_front), .pop_ready(unit_pop)
    );
    task fifo_step;
        input push;
        input [7:0] value;
        input pop;
        input want_ready;
        input want_valid;
        input [7:0] want_front;
        begin
            @(negedge clk);
            unit_push = push;
            unit_data = value;
            unit_pop = pop;
            #1;
            if (unit_ready !== want_ready || unit_valid !== want_valid)
                $fatal(1, "FIFO ready/valid mismatch");
            if (want_valid && unit_front !== want_front)
                $fatal(1, "FIFO ordering mismatch got=%02x want=%02x", unit_front, want_front);
            @(posedge clk);
            #1;
        end
    endtask

    initial begin
        #100;
        @(negedge clk); unit_rst_n = 1;
        fifo_step(1, 8'h10, 0, 1, 0, 0);
        fifo_step(1, 8'h11, 0, 1, 1, 8'h10);
        fifo_step(1, 8'h12, 0, 1, 1, 8'h10);
        fifo_step(1, 8'h13, 0, 1, 1, 8'h10);
        fifo_step(1, 8'hff, 0, 0, 1, 8'h10);
        fifo_step(1, 8'h14, 1, 1, 1, 8'h10);
        fifo_step(0, 0, 1, 1, 1, 8'h11);
        fifo_step(0, 0, 1, 1, 1, 8'h12);
        fifo_step(0, 0, 1, 1, 1, 8'h13);
        fifo_step(0, 0, 1, 1, 1, 8'h14);
        fifo_step(0, 0, 1, 1, 0, 0);
        @(negedge clk); unit_pop = 0;
        fifo_done = 1;
    end

    initial begin
        #200;
        @(negedge clk); rst_n = 1;
        #(HOST_BIT_NS * 2 + 37);
        // A short false start must not generate a byte or sticky error.
        rxd = 0;
        #(HOST_BIT_NS / 4);
        rxd = 1;
        #(HOST_BIT_NS * 2);
        if (rx_pulses != 0 || error_pulses != 0) $fatal(1, "False start accepted");

        for (i = 0; i < 256; i = i + 1) send_byte(i[7:0], HOST_BIT_NS);
        for (i = 0; i < 512; i = i + 1) begin
            lfsr = {lfsr[30:0], lfsr[31] ^ lfsr[21] ^ lfsr[1] ^ lfsr[0]};
            send_byte(lfsr[7:0], HOST_BIT_NS);
        end
        wait_for_echoes;
        // Test non-aligned phase and host baud error in both directions.
        #73;
        for (i = 0; i < 64; i = i + 1) send_byte(i[7:0] ^ 8'h55, 8508);
        wait_for_echoes;
        #91;
        for (i = 0; i < 64; i = i + 1) send_byte(i[7:0] ^ 8'haa, 8854);
        wait_for_echoes;
        if (led[3] !== 0) $fatal(1, "Unexpected framing or overflow error");

        saved_rx = rx_pulses;
        saved_errors = error_pulses;
        // Invalid stop bit followed by a break: one error, no accepted data.
        rxd = 0;
        #(HOST_BIT_NS * 24);
        rxd = 1;
        #(HOST_BIT_NS * 3);
        if (rx_pulses != saved_rx || error_pulses != saved_errors + 1 || led[3] !== 1)
            $fatal(1, "Framing/break handling failed");
        send_byte(8'ha5, HOST_BIT_NS);
        wait_for_echoes;

        // Reset during an incomplete byte; discard it, restore idle TX.
        rxd = 0;
        #(HOST_BIT_NS * 4);
        @(negedge clk); rst_n = 0; rxd = 1;
        #200;
        @(negedge clk); rst_n = 1;
        #(HOST_BIT_NS * 3);
        if (txd !== 1 || led[3] !== 0) $fatal(1, "Reset recovery failed");
        for (i = 0; i < 64; i = i + 1) send_byte(8'hff - i[7:0], HOST_BIT_NS);
        wait_for_echoes;
        if (!fifo_done) $fatal(1, "FIFO boundary test did not finish");
        $display("UART_ECHO_TB_PASS bytes=%0d fifo_boundary=PASS false_start=PASS baud_drift=PASS break=PASS reset=PASS", received_count);
        $finish;
    end
    initial begin
        #300_000_000;
        $fatal(1, "UART echo simulation timeout");
    end
endmodule
