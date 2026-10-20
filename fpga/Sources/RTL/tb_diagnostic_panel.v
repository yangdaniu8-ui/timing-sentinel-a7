`timescale 1ns/1ps
module tb_diagnostic_panel;
    reg clk=0;always #5 clk=~clk;
    reg rst_n=0,arm=0,profile_ok=1,have_a=1,gpio_response=0,mute_key_n=1;
    reg [2:0] state=0;
    reg [1:0] have_result=3;
    reg [3:0] gpio_result=1,spi_result=1;
    wire response_led,good_led,bad_led,beep;
    integer checks=0,cycles,on_cycles;
    diagnostic_panel #(.MS_CYCLES(4),.MUTE_MS(3),.RESPONSE_MS(20),
        .GOOD_BEEP_MS(5),.FAULT_SLOT_MS(4)) dut(
        .clk(clk),.rst_n(rst_n),.arm(arm),.state(state),.profile_ok(profile_ok),
        .have_a(have_a),.have_result(have_result),.gpio_response(gpio_response),
        .gpio_result(gpio_result),.spi_result(spi_result),.mute_key_n(mute_key_n),
        .response_led(response_led),.good_led(good_led),.bad_led(bad_led),.beep(beep));
    task check;input yes;input [511:0] label;
        begin if(!yes)$fatal(1,"PANEL_FAIL %0s",label);checks=checks+1;end
    endtask
    task start;
        begin state=0;arm=1;@(negedge clk);arm=0;state=2;@(negedge clk);end
    endtask
    task freeze;
        begin state=4;repeat(4)@(negedge clk);end
    endtask
    initial begin
        repeat(4)@(negedge clk);check(!beep && !good_led && !bad_led,"silent reset");
        rst_n=1;start();gpio_response=1;@(negedge clk);gpio_response=0;
        check(response_led,"real edge visible");freeze();
        check(good_led && !bad_led && beep,"normal short sound, no false timeout alarm");
        repeat(30)@(negedge clk);check(good_led && !beep,"success retained and sound bounded");
        repeat(90)@(negedge clk);check(!response_led,"edge stretch bounded");
        gpio_result=2;spi_result=1;start();freeze();
        check(!good_led && bad_led && beep,"early is fault");
        on_cycles=0;
        for(cycles=0;cycles<90;cycles=cycles+1)begin
            @(negedge clk);if(beep)on_cycles=on_cycles+1;
        end
        check(on_cycles>25 && on_cycles<60 && !beep && bad_led,"three finite fault envelopes");
        gpio_result=1;spi_result=3;start();freeze();check(bad_led && beep,"wrong SPI faults despite GPIO PASS");
        mute_key_n=0;repeat(4)@(negedge clk);mute_key_n=1;repeat(4)@(negedge clk);
        check(!dut.muted,"button bounce rejected");
        mute_key_n=0;repeat(20)@(negedge clk);
        check(dut.muted && !beep && bad_led,"mute does not clear verdict or evidence");
        start();freeze();check(!beep,"held mute stays muted across arm");
        mute_key_n=1;repeat(4)@(negedge clk);start();freeze();check(beep,"new arm enables sound after release");
        gpio_result=3;spi_result=3;start();freeze();check(bad_led,"missing or late faults");
        gpio_result=5;start();freeze();check(!good_led && !bad_led && !beep,"incomplete not fabricated as fault");
        gpio_result=4;start();freeze();check(!good_led && !bad_led && !beep,"cancel silent");
        gpio_result=1;spi_result=1;have_a=0;start();freeze();check(!good_led && !bad_led && !beep,"no A silent");
        have_a=1;have_result=1;start();freeze();check(!good_led && !bad_led && !beep,"pending result silent");
        have_result=3;profile_ok=0;start();gpio_response=1;@(negedge clk);gpio_response=0;freeze();
        check(!response_led && !good_led && !bad_led && !beep,"unsupported config silent");
        rst_n=0;repeat(2)@(negedge clk);check(!beep,"reset immediately suppresses sound");
        $display("DIAGNOSTIC_PANEL_TB_PASS checks=%0d",checks);$finish;
    end
    initial begin #100000;$fatal(1,"PANEL_TIMEOUT");end
endmodule
