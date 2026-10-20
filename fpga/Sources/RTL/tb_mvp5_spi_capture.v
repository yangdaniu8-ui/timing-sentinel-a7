`timescale 1ns/1ps
module tb_mvp5_spi_capture;
 reg clk=0;always #5 clk=~clk;
 reg rst_n=0;reg [7:0] raw=8'h11;wire ready;reg valid=0;reg [7:0] cmd=0;
 reg [15:0] length=0;reg [799:0] args=0,cfg;
 wire done;wire [15:0] status;wire [10:0] blen;wire [3295:0] body;
 wire stream;reg [10:0] index=0;wire [7:0] data;wire [2:0] state;wire [31:0] session,config_id;
 integer checks=0,waited,i,j,n,evbytes,spis,errs,spimode;
 reg [3295:0] meta;reg [47:0] atick,last_spi_tick;reg [127:0] event_copy;reg [799:0] panel_cfg;
 wire panel_arm,panel_profile_ok,panel_have_a,panel_gpio_response;
 wire [1:0] panel_have_result;wire [3:0] panel_gpio_result,panel_spi_result;
 wire response_led,good_led,bad_led,panel_beep;
 mvp5_capture #(.CLOCK_HZ(1382400),.ADDR_BITS(8)) dut(.clk(clk),.rst_n(rst_n),
  .raw_inputs(raw),.target_ready(ready),.command_valid(valid),.command(cmd),.argument_length(length),
  .arguments(args),.command_done(done),.command_status(status),.body_length(blen),
  .body_snapshot(body),.stream_response(stream),.stream_index(index),.stream_data(data),
  .state(state),.session_id(session),.config_id(config_id),.panel_arm(panel_arm),
  .panel_profile_ok(panel_profile_ok),.panel_have_a(panel_have_a),
  .panel_gpio_response(panel_gpio_response),.panel_have_result(panel_have_result),
  .panel_gpio_result(panel_gpio_result),.panel_spi_result(panel_spi_result));
 // Existing scaled test timings need an explicit observer-profile override.
 // Production qualifies only the real 50MHz canonical GPIO/SPI configuration.
 diagnostic_panel #(.MS_CYCLES(10)) panel(.clk(clk),.rst_n(rst_n),.arm(panel_arm),
  .state(state),.profile_ok(1'b1),.have_a(panel_have_a),.gpio_response(panel_gpio_response),
  .have_result(panel_have_result),.gpio_result(panel_gpio_result),.spi_result(panel_spi_result),
  .mute_key_n(1'b1),.response_led(response_led),.good_led(good_led),.bad_led(bad_led),.beep(panel_beep));
 task check;input condition;input [511:0] label;
 begin if(!condition)$fatal(1,"MVP5_SPI_FAIL %0s state=%0d",label,state);checks=checks+1;end endtask
 task req;input [7:0] c;input [15:0] len;input [799:0] a;input [15:0] expected;
 begin @(negedge clk);cmd=c;length=len;args=a;valid=1;@(negedge clk);valid=0;waited=0;
  while(!done)begin @(negedge clk);waited=waited+1;if(waited>100)$fatal(1,"backend hung");end
  check(status==expected,"command status");end endtask
 task send_uart;input [7:0] v;integer b;
 begin raw[0]=0;repeat(12)@(negedge clk);
  for(b=0;b<8;b=b+1)begin raw[0]=v[b];repeat(12)@(negedge clk);end
  raw[0]=1;repeat(12)@(negedge clk);end endtask
 task send_spi;input [7:0] v;input integer bits;integer b;
 begin raw[4]=0;repeat(8)@(negedge clk);
  for(b=7;b>=8-bits;b=b-1)begin raw[3]=v[b];repeat(8)@(negedge clk);
   raw[2]=!spimode[1];repeat(8)@(negedge clk);raw[2]=spimode[1];repeat(8)@(negedge clk);end
  raw[4]=1;repeat(8)@(negedge clk);end endtask
 task commit;begin req(8'h10,100,cfg,0);req(8'h11,0,0,0);end endtask
 initial begin
  repeat(5)@(negedge clk);rst_n=1;repeat(5)@(negedge clk);
  cfg=0;cfg[7:0]=5;cfg[23:16]=15;cfg[24]=1;cfg[95:80]=16'h11a;
  cfg[32+:32]=115200;cfg[64+:16]=10;cfg[96+:32]=127;cfg[128+:32]=128;
  for(j=0;j<4;j=j+1)begin cfg[176+j*160+:8]=8'ha5;cfg[184+j*160+:8]=8'hff;cfg[272+j*160+:48]=1000;end
  cfg[160+:8]=1;cfg[320+:8]=1;cfg[192+:8]=2;cfg[224+:48]=200;cfg[272+:48]=500;cfg[432+:48]=200;
  cfg[520+:8]=1;cfg[528+:8]=8'hc3;cfg[536+:8]=8'hff;
  cfg[680+:8]=1;cfg[688+:8]=8'h3c;cfg[696+:8]=8'hff;
  args=cfg;args[95:80]=0;req(8'h10,100,args,4); // Channel collisions rejected.
  args=cfg;args[24]=0;req(8'h10,100,args,4); // Rules cannot use disabled monitor.
  args=cfg;args[520+:8]=2;req(8'h10,100,args,4);
  args=cfg;args[528+:8]=1;args[536+:8]=0;req(8'h10,100,args,4);
  panel_cfg=cfg;panel_cfg[224+:48]=25000;panel_cfg[272+:48]=100000;
  panel_cfg[544+:48]=0;panel_cfg[592+:48]=150000;
  req(8'h10,100,panel_cfg,0);req(8'h11,0,0,0);check(panel_profile_ok,"canonical production panel profile");
  panel_cfg[528+:8]=8'h3c;req(8'h10,100,panel_cfg,0);req(8'h11,0,0,0);
  check(!panel_profile_ok,"different SPI expectation not silently labelled normal");
  for(spimode=0;spimode<4;spimode=spimode+1)begin
   cfg[26:25]=spimode;commit();
   for(n=0;n<6;n=n+1)begin
    raw=8'h11;raw[2]=spimode[1];req(8'h20,4,config_id,0);waited=0;
    while(!ready)begin @(negedge clk);waited=waited+1;if(waited>3000)$fatal(1,"READY timeout");end
    repeat(20)@(negedge clk);send_uart(8'ha5);atick=dut.selected_a[0];
    check(dut.have_a==15,"GPIO and SPI rules share UART A");
    j=n==1 ? 50 : n==2 ? 550 : 300;
    while(dut.tick-atick<j)@(negedge clk);
    if(n!=3)begin raw[1]=1;send_spi(n==4 ? 8'h3c : 8'hc3,n==5 ? 3 : 8);raw[1]=0;end
    waited=0;while(state!=4)begin @(negedge clk);waited=waited+1;if(waited>6000)$fatal(1,"freeze timeout");end
    req(8'h22,4,session,0);meta=body;
    check(good_led==(n==0) && bad_led==(n!=0),"panel verdict follows actual GPIO/SPI results");
    check(meta[0+:8]==5 && meta[2496+:800]==cfg,"schema5/config evidence");
    check(meta[456+:8]==(n==1 ? 2 : n==2 || n==3 ? 3 : 1),"GPIO rule0 matrix");
    check(meta[968+:8]==(n==1 ? 1 : 3),"GPIO strict rule1 matrix");
    check(meta[1480+:8]==(n>=3 ? 3 : 1),"C3 rule ignores wrong/truncated SPI");
    check(meta[1992+:8]==(n==4 ? 1 : 3),"3C rule only matches wrong-byte scene");
    check(meta[408+:8]==0,"SPI transactions continue through POST");
    evbytes=meta[176+:16]*16;req(8'h30,10,{720'd0,16'd992,32'd0,session},0);
    spis=0;errs=0;last_spi_tick=0;
    for(i=0;i<evbytes;i=i+16)begin
     event_copy=0;for(j=0;j<16;j=j+1)begin index=i+j;repeat(2)@(negedge clk);event_copy[j*8+:8]=data;end
     if(event_copy[112])begin spis=spis+1;check(event_copy[120:113]==(n==4 ? 8'h3c : 8'hc3),"SPI event byte");last_spi_tick=event_copy[47:0];end
     if(event_copy[121])errs=errs+1;
     check(event_copy[127:122]==0,"reserved event bits");
    end
    check(spis==(n<3 || n==4 ? 1 : 0) && errs==(n==5 ? 1 : 0),"SPI event count/truncation");
    if(spis)check(last_spi_tick==meta[(n==4 ? 2064 : 1552)+:48],"B uses eighth sample edge exact tick");
    req(8'h32,4,session,0);
   end
  end
  $display("MVP5_SPI_CAPTURE_TB_PASS checks=%0d cases=24",checks);$finish;
 end
 initial begin #30000000;$fatal(1,"MVP5_SPI_TIMEOUT");end
endmodule
