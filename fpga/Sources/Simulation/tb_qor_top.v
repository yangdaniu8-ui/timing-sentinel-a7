`timescale 1ns/1ps
// Only external board ports are driven/observed: valid for flattened netlists.
module tb_qor_top;
    localparam integer BIT_CYCLES=434;
    localparam integer BIT_TIME_NS=BIT_CYCLES*20;
    reg clk=0;
    always #10 clk=~clk;
    reg rst_n=0, rx=1;
    wire tx, ready, beep;
    wire [3:0] led;
    mvp5_top dut(.sys_clk(clk), .sys_rst_n(rst_n), .uart_rxd(rx),
        .uart_txd(tx), .led(led), .probe_inputs(8'h11), .target_ready(ready),
        .beep(beep), .mute_key_n(1'b1), .start_key_n(1'b1), .release_key_n(1'b1));
    reg [31:0] vectors[0:4095];
    reg [7:0] received[0:2047];
    reg [4095:0] vector_file;
    reg [7:0] decoded;
    integer count=0, checked=0, index=0, k, waited;

    // Receiver samples the actual FPGA serial output at each bit centre.
    initial begin
        forever begin
            @(negedge tx);
            #(BIT_TIME_NS/2);
            if (tx !== 1'b0) $fatal(1,"QOR_TOP bad TX start");
            for (k=0;k<8;k=k+1) begin
                #BIT_TIME_NS;
                decoded[k]=tx;
            end
            #BIT_TIME_NS;
            if (tx !== 1'b1) $fatal(1,"QOR_TOP bad TX stop");
            if (count>=2048) $fatal(1,"QOR_TOP RX bounds");
            received[count]=decoded;
            count=count+1;
        end
    end
    task send_byte;
        input [7:0] value;
        integer b;
        begin
            @(negedge clk); rx=0;
            repeat(BIT_CYCLES) @(negedge clk);
            for(b=0;b<8;b=b+1) begin
                rx=value[b];
                repeat(BIT_CYCLES) @(negedge clk);
            end
            rx=1;
            repeat(BIT_CYCLES+2) @(negedge clk);
        end
    endtask
    initial begin
        if (!$value$plusargs("VECTORS=%s",vector_file)) $fatal(1,"QOR_TOP missing vectors");
        $readmemh(vector_file,vectors);
        repeat(12) @(negedge clk);
        rst_n=1;
        repeat(8) @(negedge clk);
        while(vectors[index] !== 32'hff000000) begin
            case(vectors[index][31:24])
                1:send_byte(vectors[index][7:0]);
                2:begin
                    waited=0;
                    while(count<=checked && waited<2000000) begin
                        @(negedge clk); waited=waited+1;
                    end
                    if(count<=checked) $fatal(1,"QOR_TOP missing byte=%0d",checked);
                    if(received[checked] !== vectors[index][7:0])
                        $fatal(1,"QOR_TOP byte=%0d got=%02x expected=%02x",checked,received[checked],vectors[index][7:0]);
                    checked=checked+1;
                end
                default:$fatal(1,"QOR_TOP invalid vector=%0d",index);
            endcase
            index=index+1;
            if(index>=4096) $fatal(1,"QOR_TOP vector bounds");
        end
        repeat(50000) @(negedge clk);
        if(count!=checked || ready!==0 || beep!==0)
            $fatal(1,"QOR_TOP final count=%0d checked=%0d ready=%b beep=%b",count,checked,ready,beep);
        $display("QOR_TOP_BYTE_ORACLE_PASS bytes=%0d vectors=%0d",checked,index);
        $finish;
    end
    initial begin #200000000; $fatal(1,"QOR_TOP_GLOBAL_TIMEOUT"); end
endmodule
