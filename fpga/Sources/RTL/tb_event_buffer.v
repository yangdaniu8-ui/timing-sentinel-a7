`timescale 1ns/1ps
module tb_event_buffer;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,clear=0,write_enable=0,frozen=0,read_enable=0;
    reg [127:0] data;
    reg [9:0] address;
    wire [15:0] count;
    wire [31:0] overwrites;
    wire [127:0] received;
    wire valid;
    integer i,checks=0;
    event_buffer dut(.clk(clk),.rst_n(rst_n),.clear(clear),.write_enable(write_enable),
        .event_data(data),.count(count),.overwrites(overwrites),.frozen(frozen),
        .read_enable(read_enable),.read_address(address),.read_data(received),.read_valid(valid));
    initial begin
        repeat(3) @(negedge clk);rst_n=1;
        for(i=0;i<10000;i=i+1) begin
            @(negedge clk);write_enable=1;data={64'hA7351234F00DCAFE,32'd0,i[31:0]};
        end
        @(negedge clk);write_enable=0;frozen=1;
        #1;if(count!=1024 || overwrites!=8976) $fatal(1,"Ring metadata");
        for(i=0;i<1024;i=i+1) begin
            @(negedge clk);read_enable=1;address=i;
            @(posedge clk);#1;
            if(!valid || received!=={64'hA7351234F00DCAFE,32'd0,32'd8976+i}) $fatal(1,"Ring ordering %0d",i);
            checks=checks+1;
        end
        @(negedge clk);clear=1;frozen=0;write_enable=1;data=128'hdead;
        @(posedge clk);#1;
        if(count!=0 || overwrites!=0 || valid) $fatal(1,"Clear did not invalidate");
        @(negedge clk);clear=0;write_enable=1;data=128'hcafe;
        @(posedge clk);#1;
        @(negedge clk);write_enable=0;frozen=1;address=0;
        @(posedge clk);#1;if(!valid || received!==128'hcafe) $fatal(1,"Reuse after clear");
        @(negedge clk);address=1;
        @(posedge clk);#1;if(valid) $fatal(1,"Invalid read accepted");
        $display("EVENT_BUFFER_TB_PASS WRITES=10000 READ_CHECKS=%0d",checks);$finish;
    end
endmodule
