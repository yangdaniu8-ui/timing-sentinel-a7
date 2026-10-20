`timescale 1ns/1ps
module tb_uart_gpio_rule;
    reg clk=0; always #10 clk=~clk;
    reg rst_n=0,enable=0,cancel=0,uart_valid=0,gpio_edge=0;
    reg [47:0] tick=0,min_ticks=0,max_ticks=0;
    reg [7:0] uart_data=0,value=0,mask=0;
    wire busy,accepted,overlap,result_valid,matched_b;
    wire [3:0] result;
    wire [47:0] result_a,result_b;
    uart_gpio_rule dut(.clk(clk),.rst_n(rst_n),.enable(enable),.cancel(cancel),.logic_tick(tick),
        .uart_valid(uart_valid),.uart_data(uart_data),.byte_value(value),.byte_mask(mask),
        .gpio_edge(gpio_edge),.min_ticks(min_ticks),.max_ticks(max_ticks),.busy(busy),
        .accepted(accepted),.overlap(overlap),.result_valid(result_valid),.result(result),
        .result_a(result_a),.result_b(result_b),.matched_b(matched_b));
    reg [255:0] vector;
    reg [2047:0] path;
    integer fd,rc,checks=0,results=0,overlaps=0;
    initial begin
        repeat(3) @(negedge clk);rst_n=1;
        if(!$value$plusargs("RULE_VECTORS=%s",path)) $fatal(1,"Missing vectors");
        fd=$fopen(path,"r");if(!fd) $fatal(1,"Cannot open vectors");
        while(!$feof(fd)) begin
            rc=$fscanf(fd,"%h\n",vector);
            if(rc==1) begin
                @(negedge clk);
                tick=vector[47:0];min_ticks=vector[95:48];max_ticks=vector[143:96];
                uart_data=vector[151:144];value=vector[159:152];mask=vector[167:160];
                enable=vector[168];cancel=vector[169];uart_valid=vector[170];gpio_edge=vector[171];
                #1;
                if(busy!==vector[172] || accepted!==vector[173] || overlap!==vector[174] ||
                    result!==vector[178:175] || result_valid!==(vector[178:175]!=0))
                    $fatal(1,"Rule mismatch at row %0d tick %0d result %0d expected %0d busy %0b/%0b",checks,tick,result,vector[178:175],busy,vector[172]);
                if(result_valid && (result_a!==vector[226:179] ||
                    matched_b!==(result==1 || result==2) || result_b!==(matched_b ? tick : 48'd0)))
                    $fatal(1,"Result timestamp mismatch row %0d",checks);
                if(result_valid) results=results+1;
                if(overlap) overlaps=overlaps+1;
                @(posedge clk);#1;
                if(busy!==vector[227]) $fatal(1,"Busy after row %0d",checks);
                checks=checks+1;
            end
        end
        $fclose(fd);
        if(checks<100000 || results<1000 || overlaps<1000) $fatal(1,"Insufficient coverage");
        $display("UART_GPIO_RULE_TB_PASS CHECKS=%0d RESULTS=%0d OVERLAPS=%0d",checks,results,overlaps);$finish;
    end
    initial begin #100000000; $fatal(1,"Rule test watchdog"); end
endmodule
