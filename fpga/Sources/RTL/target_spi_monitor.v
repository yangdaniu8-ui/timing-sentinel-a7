`timescale 1ns/1ps
// Passive 8-bit MSB-first MOSI monitor. ALL inputs are already synchronized
// into clk; SCK is data, never a fabric clock. Valid is the eighth sample edge,
// not CS deassertion. Every SCK half-period and MOSI setup/hold >=4 clk ticks
// is required at the connector (initial verified stimulus: 250 kHz).
// Arming mid-CS skips that frame until CS goes high. A truncated byte produces
// frame_error; byte-aligned CS endings and idle clocks produce no error.
module target_spi_monitor(
 input wire clk,input wire rst_n,input wire clear,
 input wire sck,input wire cs_n,input wire mosi,input wire [1:0] mode,
 output wire byte_valid,output wire frame_error,output wire [7:0] data
);
 reg previous_sck,previous_cs,qualified,running;
 reg [2:0] count;reg [7:0] shift;
 wire edge_seen=sck!=previous_sck;
 wire sample_edge=edge_seen && (mode[0] ? sck==mode[1] : sck!=mode[1]);
 wire enabled=running && !clear && qualified;
 assign byte_valid=enabled && !cs_n && !previous_cs && sample_edge && count==7;
 assign frame_error=enabled && cs_n && !previous_cs && count!=0;
 assign data={shift[6:0],mosi};
 always @(posedge clk) begin
  if(!rst_n) running<=0;else running<=1;
  if(!rst_n || clear) begin
   previous_sck<=sck;previous_cs<=cs_n;qualified<=0;count<=0;shift<=0;
  end else begin
   previous_sck<=sck;previous_cs<=cs_n;
   if(cs_n) begin qualified<=1;count<=0;shift<=0;end
   else if(previous_cs) begin count<=0;shift<=0;end
   else if(qualified && sample_edge) begin shift<={shift[6:0],mosi};count<=count+1'b1;end
  end
 end
endmodule
