`timescale 1ns/1ps
module tb_diagnostic_profile;
    reg clk=0;always #5 clk=~clk;
    reg [800:0] vectors[0:27];reg [1023:0] path;
    reg [799:0] cfg;wire qualified;
    reg rst_n=0,arm=0,gpio_response=0,mute_key_n=1;
    reg [2:0] state=0;reg [3:0] gpio_result=1,spi_result=1;
    wire response_led,good_led,bad_led,beep;
    integer i,j,k,checks=0,pulses=0;reg previous_beep;
    diagnostic_profile guard(.config_bytes(cfg),.qualified(qualified));
    diagnostic_panel #(.MS_CYCLES(4),.MUTE_MS(3),.RESPONSE_MS(20),
        .GOOD_BEEP_MS(5),.FAULT_SLOT_MS(4)) panel(
        .clk(clk),.rst_n(rst_n),.arm(arm),.state(state),.profile_ok(qualified),
        .have_a(1'b1),.have_result(2'b11),.gpio_response(gpio_response),
        .gpio_result(gpio_result),.spi_result(spi_result),.mute_key_n(mute_key_n),
        .response_led(response_led),.good_led(good_led),.bad_led(bad_led),.beep(beep));
    task check;input yes;input [511:0] label;
        begin if(!yes)$fatal(1,"PROFILE_FAIL vector=%0d %0s",i,label);checks=checks+1;end
    endtask
    initial begin
        if(!$value$plusargs("PANEL_VECTORS=%s",path))$fatal(1,"missing vectors");
        $readmemh(path,vectors);
        for(i=0;i<28;i=i+1)begin
            cfg=vectors[i][799:0];rst_n=0;state=0;mute_key_n=1;
            repeat(4)@(negedge clk);rst_n=1;arm=1;@(negedge clk);arm=0;state=2;
            check(qualified===vectors[i][800],"qualification matches serialized host config");
            gpio_response=1;@(negedge clk);gpio_response=0;
            check(response_led===vectors[i][800],"physical edge feedback gated by profile");
            gpio_result=(i==1) ? 3 : 1;spi_result=gpio_result;
            state=4;repeat(4)@(negedge clk);
            if(vectors[i][800])begin
                check(beep && good_led==(i!=1) && bad_led==(i==1),"real task PASS or TIMEOUT feedback");
                pulses=beep ? 1 : 0;previous_beep=beep;
                for(j=0;j<90;j=j+1)begin
                    @(negedge clk);if(beep && !previous_beep)pulses=pulses+1;previous_beep=beep;
                end
                check(!beep && pulses==(i==1 ? 3 : 1),"one success or three fault pulses");
                state=0;arm=1;@(negedge clk);arm=0;state=2;
                mute_key_n=0;repeat(20)@(negedge clk);state=4;repeat(4)@(negedge clk);
                check(!beep && (good_led || bad_led),"KEY3 mute preserves diagnosis in all profiles");
            end else check(!good_led && !bad_led && !beep,"unsupported config remains silent");
        end
        $display("DIAGNOSTIC_PROFILE_TB_PASS checks=%0d vectors=28",checks);$finish;
    end
    initial begin #1000000;$fatal(1,"PROFILE_TIMEOUT");end
endmodule
