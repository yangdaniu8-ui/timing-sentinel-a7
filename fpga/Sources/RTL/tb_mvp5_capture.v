`timescale 1ns/1ps
module tb_mvp5_capture;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0;reg [7:0] raw=1;wire ready;reg valid=0;reg [7:0] cmd=0;
 reg [15:0] length=0;reg [799:0] args=0,cfg;
 wire done;wire [15:0] status;wire [10:0] blen;wire [3295:0] body;
 wire stream;reg [10:0] index=0;wire [7:0] data;wire [2:0] state;wire [31:0] session,config_id;
 integer checks=0,waited,i,j,n;reg [3295:0] meta;reg [47:0] atick;
 reg [1007:0] held_status;
 reg [7:0] history[0:131071];integer sample_checks=0;
 always @(posedge clk) if(rst_n) history[dut.tick[16:0]]<=dut.levels;
 mvp5_capture #(.CLOCK_HZ(1382400),.ADDR_BITS(8)) dut(.clk(clk),.rst_n(rst_n),
  .raw_inputs(raw),.target_ready(ready),.command_valid(valid),.command(cmd),.argument_length(length),
  .arguments(args),.command_done(done),.command_status(status),.body_length(blen),
  .body_snapshot(body),.stream_response(stream),.stream_index(index),.stream_data(data),
  .state(state),.session_id(session),.config_id(config_id));
 task check;input condition;input [511:0] label;
 begin if(!condition) $fatal(1,"MVP5_CAPTURE_FAIL %0s state=%0d tick=%0d",label,state,dut.tick);checks=checks+1;end endtask
 task req;input [7:0] c;input [15:0] len;input [799:0] a;input [15:0] expected;
 begin @(negedge clk);cmd=c;length=len;args=a;valid=1;@(negedge clk);valid=0;waited=0;
  while(!done) begin @(negedge clk);waited=waited+1;if(waited>100) $fatal(1,"backend hung");end
  check(status==expected,"command status");end endtask
 task commit;begin req(8'h10,100,cfg,0);req(8'h11,0,0,0);req(8'h12,0,0,0);
  check(body[32+:800]==cfg && body[31:0]==config_id,"atomic 100B readback");end endtask
 task arm_capture;begin req(8'h20,4,config_id,0);waited=0;
  while(!ready) begin @(negedge clk);waited=waited+1;if(waited>20000) $fatal(1,"no READY");end
  repeat(20) @(negedge clk);end endtask
 task send_byte;input [7:0] v;integer b;
 begin raw[cfg[10:8]]=0;repeat(12) @(negedge clk);
  for(b=0;b<8;b=b+1) begin raw[cfg[10:8]]=v[b];repeat(12) @(negedge clk);end
  raw[cfg[10:8]]=1;repeat(12) @(negedge clk);end endtask
 task freeze;begin waited=0;
  while(state!=4) begin @(negedge clk);waited=waited+1;if(waited>30000) $fatal(1,"no freeze");end
  req(8'h22,4,session,0);meta=body;check(blen==412 && meta[2496+:800]==cfg,"META5 size/config");
  repeat(100) begin @(negedge clk);check(body===meta,"frozen META response stays stable");end
  check(meta[128+:32]==256,"full samples");req(8'h31,10,{720'd0,16'd256,32'd0,session},0);
  for(i=0;i<256;i=i+1) begin index=i;repeat(2) @(negedge clk);
   check(data==history[(meta[224+:48]+i*cfg[64+:16])%131072],"chronological sample/tick");sample_checks=sample_checks+1;end
 end endtask
 task release_record;begin req(8'h32,4,session+1,6);req(8'h32,4,session,0);check(state==0,"release");end endtask
 task standard;begin
  cfg=0;cfg[7:0]=5;cfg[23:16]=15;cfg[32+:32]=115200;cfg[64+:16]=10;cfg[96+:32]=127;cfg[128+:32]=128;
  for(j=0;j<4;j=j+1) begin cfg[160+j*160+:8]=1;cfg[176+j*160+:8]=8'ha5;cfg[184+j*160+:8]=8'hff;end
  cfg[192+:8]=2;cfg[224+:48]=200;cfg[272+:48]=500;
  cfg[432+:48]=200;cfg[592+:48]=800;
  cfg[648+:8]=1;cfg[704+:48]=250;cfg[752+:48]=800;
 end endtask
 initial begin
  repeat(5) @(negedge clk);rst_n=1;repeat(5) @(negedge clk);
  req(2,0,0,0);check(blen==34 && body[7:0]==5 && body[95:88]==4,"INFO four rules plus SPI");
  standard();args=cfg;args[23:16]=0;req(8'h10,100,args,4);
  args=cfg;args[200+:8]=1;req(8'h10,100,args,4);
  args=cfg;args[192+:8]=3;req(8'h10,100,args,4);
  req(8'h10,99,cfg,3);commit();check(dut.period==12,"baud division");
  for(n=0;n<4;n=n+1) begin
   raw=1;arm_capture();req(8'h23,0,0,0);held_status=body[1007:0];
   send_byte(8'hd0+n);repeat(24) @(negedge clk);send_byte(8'ha5);
   atick=dut.selected_a[0];check(dut.have_a==15,"four simultaneous accepted A");
   if(n!=3) begin j=n==0 ? 300 : n==1 ? 100 : 550;
    while(dut.tick-atick<j) @(negedge clk);raw[1]=1;repeat(20) @(negedge clk);raw[1]=0;end
   check(body[1007:0]===held_status,"live STATUS remains atomic across events");
   freeze();
   check(meta[456+:8]==(n==0 ? 1 : n==1 ? 2 : 3),"rule0 matrix");
   check(meta[968+:8]==(n==1 ? 1 : 3),"rule1 short deadline matrix");
   check(meta[1480+:8]==(n==3 ? 3 : 1),"rule2 broad deadline continues POST");
   check(meta[1992+:8]==(n==3 ? 3 : n==1 ? 2 : 1),"rule3 falling edge matrix");
   check(meta[400+:8]==1 && meta[16+:8]==0 && !meta[408+:8],"primary/incomplete");
   for(j=0;j<4;j=j+1) check(meta[736+j*512+:32]==1,"accepted once each");
   if(n==2) check(meta[450] && meta[624+:48]-atick>500,"late edge retained after primary TIMEOUT");
   req(8'h23,0,0,0);check(blen==126 && body[80+:8]==15,"STATUS5 size");
   req(8'h31,10,{720'd0,16'd10,32'd256,session},0);check(blen==10,"EOF");
   release_record();
  end
  // Same-beat trigger tie is mask 0101, lowest requesting rule wins.
  cfg[512+:8]=2;commit();raw=1;arm_capture();send_byte(8'ha5);repeat(300) @(negedge clk);
  raw[1]=1;repeat(20) @(negedge clk);raw[1]=0;freeze();
  check(meta[400+:8]==5 && meta[16+:8]==0,"same beat trigger tie");release_record();
  // Rule0 RECORD must not win priority against requesting rule1.
  standard();cfg[192+:8]=0;cfg[352+:8]=2;commit();raw=1;arm_capture();send_byte(8'ha5);
  repeat(100) @(negedge clk);raw[1]=1;repeat(20) @(negedge clk);raw[1]=0;freeze();
  check(meta[400+:8]==2 && meta[16+:8]==1,"priority only requests");release_record();
  // Long outstanding rule gets explicit INCOMPLETE_CAPTURE_END, never timeout.
  standard();cfg[592+:48]=10000;commit();raw=1;arm_capture();send_byte(8'ha5);
  repeat(2000) @(negedge clk);freeze();
  check(meta[1480+:8]==5 && meta[408+:8]==4 && meta[1920+:32]==1 && meta[1856+:32]==0,"post cutoff semantics");release_record();
  // DISARM cancels all four pending, no timeout misclassification.
  standard();commit();raw=1;arm_capture();send_byte(8'ha5);req(8'h21,4,session,0);
  req(8'h22,4,session,0);
  for(j=0;j<4;j=j+1) check(body[456+j*512+:8]==4 && body[864+j*512+:32]==1,"cancel all pending");
  release_record();
  raw=1;arm_capture();send_byte(8'hb5);req(8'h21,4,session,0);req(8'h22,4,session,0);
  check(body[9] && !body[8] && body[16+:8]==255,"manual no A");release_record();
  // Disabled slots are canonical zero and never act.
  cfg[23:16]=1;cfg[320+:480]=0;commit();raw=1;arm_capture();send_byte(8'ha5);
  repeat(300) @(negedge clk);raw[1]=1;repeat(20) @(negedge clk);raw[1]=0;freeze();
  check(meta[968+:512]==0 && meta[1480+:8]==0,"disabled slots");release_record();
  $display("MVP5_CAPTURE_TB_PASS checks=%0d samples=%0d",checks,sample_checks);$finish;
 end
 initial begin #30000000;$fatal(1,"MVP5_GLOBAL_TIMEOUT");end
endmodule

