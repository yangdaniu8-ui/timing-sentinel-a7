`timescale 1ns/1ps

// Directed, public-interface test for the exact 64 KiB capture boundary.
// Uses the frozen qualified RTL as-is; no force/deposit or internal writes.
module tb_boundary_capture;
    reg clk = 0;
    always #10 clk = ~clk;

    reg rst_n = 0;
    reg [7:0] raw_inputs = 8'ha5;
    reg command_valid = 0;
    reg [7:0] command = 0;
    reg [15:0] argument_length = 0;
    reg [799:0] arguments = 0;
    reg [10:0] stream_index = 0;
    wire target_ready, command_done, stream_response;
    wire [15:0] command_status;
    wire [10:0] body_length;
    wire [3295:0] body_snapshot;
    wire [7:0] stream_data;
    wire [2:0] state;
    wire [31:0] session_id, config_id;

    reg [799:0] cfg = 0;
    integer checks = 0;
    integer cycles = 0;
    integer i;

    mvp5_capture #(.CLOCK_HZ(50000000), .ADDR_BITS(16)) dut (
        .clk(clk), .rst_n(rst_n), .raw_inputs(raw_inputs), .target_ready(target_ready),
        .command_valid(command_valid), .command(command),
        .argument_length(argument_length), .arguments(arguments),
        .command_done(command_done), .command_status(command_status),
        .body_length(body_length), .body_snapshot(body_snapshot),
        .stream_response(stream_response), .stream_index(stream_index),
        .stream_data(stream_data), .state(state),
        .session_id(session_id), .config_id(config_id)
    );

    task check;
        input condition;
        input [511:0] label;
        begin
            if (!condition)
                $fatal(1, "BOUNDARY_FAIL %0s state=%0d status=%0d blen=%0d time=%0t",
                       label, state, command_status, body_length, $time);
            checks = checks + 1;
        end
    endtask

    task request;
        input [7:0] code;
        input [15:0] length;
        input [799:0] payload;
        input [15:0] expected_status;
        integer waited;
        begin
            @(negedge clk);
            command = code;
            argument_length = length;
            arguments = payload;
            command_valid = 1;
            @(negedge clk);
            command_valid = 0;
            waited = 0;
            while (!command_done) begin
                @(negedge clk);
                waited = waited + 1;
                if (waited > 100)
                    $fatal(1, "BOUNDARY_FAIL command timeout code=%02x", code);
            end
            check(command_status == expected_status, "response status");
        end
    endtask

    task read_samples;
        input [31:0] offset;
        input [15:0] count;
        input [15:0] expected_status;
        input [10:0] expected_body_length;
        begin
            request(8'h31, 16'd10,
                    {720'd0, count, offset, session_id}, expected_status);
            check(body_length == expected_body_length, "response length");
            if (expected_status == 0)
                check(stream_response, "stream response enabled");
        end
    endtask

    initial begin
        repeat (5) @(negedge clk);
        rst_n = 1;
        repeat (5) @(negedge clk);

        // One inert GPIO rule; divider 1 fills all 65,536 sample slots.
        cfg[7:0] = 5;
        cfg[23:16] = 1;
        cfg[32+:32] = 115200;
        cfg[64+:16] = 1;
        cfg[96+:32] = 32767;
        cfg[128+:32] = 32768;
        cfg[160+:8] = 1;
        cfg[176+:8] = 8'ha5;
        cfg[184+:8] = 8'hff;
        cfg[192+:8] = 2;
        cfg[224+:48] = 25000;
        cfg[272+:48] = 100000;

        request(8'h10, 16'd100, cfg, 0);
        request(8'h11, 16'd0, 0, 0);
        request(8'h20, 16'd4, config_id, 0);

        // Wait for prefill and another full ring; no synthetic internal state.
        while (cycles < 65550) begin
            @(negedge clk);
            cycles = cycles + 1;
            if (cycles == 50000)
                raw_inputs = 8'he5;
        end
        check(target_ready, "prefill complete");
        request(8'h21, 16'd4, session_id, 0);
        check(state == 4, "frozen after stop");
        request(8'h22, 16'd4, session_id, 0);
        check(body_snapshot[128+:32] == 65536, "full depth metadata");

        read_samples(32'd0, 16'd1004, 0, 11'd1014);
        for (i = 0; i < 3; i = i + 1) begin
            stream_index = i;
            repeat (3) @(negedge clk);
            check(stream_data === 8'ha5, "first block bytes");
        end

        read_samples(32'd65530, 16'd10, 0, 11'd16);
        for (i = 0; i < 6; i = i + 1) begin
            stream_index = i;
            repeat (3) @(negedge clk);
            check(stream_data === 8'he5, "clipped tail bytes");
        end

        read_samples(32'd65535, 16'd1, 0, 11'd11);
        stream_index = 0;
        repeat (3) @(negedge clk);
        check(stream_data === 8'he5, "last byte");

        read_samples(32'd65536, 16'd1, 0, 11'd10);
        read_samples(32'd65537, 16'd1, 4, 11'd0);
        read_samples(32'hffffffff, 16'd1, 4, 11'd0);
        read_samples(32'd0, 16'd1005, 4, 11'd0);
        read_samples(32'd0, 16'd0, 4, 11'd0);

        $display("BOUNDARY_CAPTURE_PASS checks=%0d depth=65536 full=1004 tail=6 eof=0", checks);
        $finish;
    end

    initial begin
        #2500000;
        $fatal(1, "BOUNDARY_CAPTURE_TIMEOUT state=%0d cycles=%0d", state, cycles);
    end
endmodule
