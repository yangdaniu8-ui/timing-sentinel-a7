`timescale 1ns/1ps
module tb_target_spi_monitor;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0,clear=1,sck=0,cs=1,mosi=0;reg [1:0] mode=0;
 wire valid,error;wire [7:0] data;integer seen=0,errors=0,checks=0,i,j,b;
 reg [7:0] received[0:63];
 target_spi_monitor dut(clk,rst_n,clear,sck,cs,mosi,mode,valid,error,data);
 always @(posedge clk) begin if(valid) begin received[seen]=data;seen=seen+1;end if(error) errors=errors+1;end
 task check;input ok;input [511:0] label;
 begin if(!ok)$fatal(1,"SPI_MONITOR_FAIL %0s",label);checks=checks+1;end endtask
 task cycles;input integer n;begin repeat(n)@(negedge clk);end endtask
 task bits;input [7:0] value;input integer n;integer k;
 begin for(k=7;k>=8-n;k=k-1) begin
  mosi=value[k];cycles(4);sck=~mode[1];cycles(4);sck=mode[1];cycles(4);
 end end endtask
 initial begin
  cycles(5);rst_n=1;cycles(3);clear=0;cycles(3);
  for(i=0;i<4;i=i+1) begin
   clear=1;mode=i;sck=mode[1];cs=1;cycles(3);clear=0;cycles(3);
   cs=0;cycles(4);bits(8'hc3,8);bits(8'h3c,8);cs=1;cycles(4);
   check(seen==(i+1)*2 && received[i*2]==8'hc3 && received[i*2+1]==8'h3c,"all four modes, contiguous bytes");
   check(errors==i,"byte-aligned end not truncated");
   cs=0;cycles(4);bits(8'ha0,3);cs=1;cycles(4);
   check(errors==i+1 && seen==(i+1)*2,"partial byte gives error not byte");
   for(j=0;j<8;j=j+1)begin sck=~sck;cycles(4);end
   check(seen==(i+1)*2,"CS-high clocks ignored");
  end
  // Clear while selected: never fabricate a suffix byte.
  mode=0;sck=0;cs=0;clear=1;cycles(4);clear=0;cycles(4);bits(8'hff,8);
  check(seen==8,"mid-frame arm ignored until CS high");cs=1;cycles(4);
  cs=0;cycles(4);bits(8'h55,8);cs=1;cycles(4);
  check(seen==9 && received[8]==8'h55,"recovery after mid-frame arm");
  clear=1;cycles(3);check(!valid && !error,"disabled gating");
  $display("TARGET_SPI_MONITOR_TB_PASS checks=%0d bytes=%0d errors=%0d",checks,seen,errors);$finish;
 end
 initial begin #1000000;$fatal(1,"SPI_MONITOR_TIMEOUT");end
endmodule
