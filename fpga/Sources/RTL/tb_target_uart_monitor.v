`timescale 1ns/1ps
module tb_target_uart_monitor;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,clear=1,raw_serial=1;
    reg [31:0] period=434;
    wire [7:0] levels,rising,falling,data;
    wire valid,error;
    integer valid_count=0,error_count=0,checks=0,last_valid_count,last_error_count,p,actual,j;
    reg [7:0] expected;
    input_frontend frontend(.clk(clk),.rst_n(rst_n),.clear_history(clear),.raw({7'd0,raw_serial}),
        .levels(levels),.rising(rising),.falling(falling));
    target_uart_monitor dut(.clk(clk),.rst_n(rst_n),.clear(clear),.serial_level(levels[0]),
        .bit_period(period),.byte_valid(valid),.frame_error(error),.data(data));
    always @(posedge clk) begin
        if(valid) begin
            valid_count=valid_count+1;
            if(data!==expected) $fatal(1,"Decoded byte mismatch %h/%h period%0d",data,expected,period);
            // Combinational valid must be observed AT stop centre, not after it.
            if(dut.timer!==0 || dut.state!==3 || !levels[0]) $fatal(1,"Invalid reference edge");
        end
        if(error) error_count=error_count+1;
    end
    task bit_value;
        input bit_level;input integer length;
        begin @(negedge clk);#3;raw_serial=bit_level;repeat(length) @(posedge clk); end
    endtask
    task send;
        input [7:0] byte_data;input integer length;input stop_level;
        integer k;
        begin
            expected=byte_data;
            bit_value(0,length);
            for(k=0;k<8;k=k+1) bit_value(byte_data[k],length);
            bit_value(stop_level,length);
        end
    endtask
    initial begin
        repeat(4) @(negedge clk);rst_n=1;clear=0;
        for(p=0;p<4;p=p+1) begin
            case(p) 0:period=50;1:period=434;2:period=1000;3:period=41667;endcase
            clear=1;repeat(3) @(negedge clk);clear=0;
            for(j=0;j<3;j=j+1) begin
                actual=j==0 ? period : j==1 ? (period*98)/100 : (period*102)/100;
                bit_value(1,period*2+8);
                last_valid_count=valid_count;last_error_count=error_count;
                send(8'ha5 ^ (j*8'h33),actual,1);bit_value(1,period+8);
                if(valid_count!=last_valid_count+1 || error_count!=last_error_count)
                    $fatal(1,"Valid packet count period%0d actual%0d",period,actual);
                checks=checks+1;
            end
            last_valid_count=valid_count;
            bit_value(0,period/4);bit_value(1,period*2+8);
            if(valid_count!=last_valid_count) $fatal(1,"False start accepted");
            last_error_count=error_count;
            send(8'ha5,period,0);bit_value(0,period);bit_value(1,period*2+8);
            if(error_count!=last_error_count+1 || valid_count!=last_valid_count) $fatal(1,"Framing error handling");
            last_valid_count=valid_count;
            send(8'h00,period,1);send(8'hff,period,1);bit_value(1,period+8);
            if(valid_count!=last_valid_count+2) $fatal(1,"Back-to-back reception");
            checks=checks+4;
        end
        $display("TARGET_UART_MONITOR_TB_PASS CHECKS=%0d VALID=%0d FRAMING_ERRORS=%0d",checks,valid_count,error_count);$finish;
    end
    initial begin #200000000;$fatal(1,"UART watchdog");end
endmodule
