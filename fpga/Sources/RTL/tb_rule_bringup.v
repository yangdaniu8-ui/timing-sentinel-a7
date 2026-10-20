`timescale 1ns/1ps
module tb_rule_bringup;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,start=0;
    reg [1:0] scenario=0;
    reg [9:0] address=0;
    reg [13:0] event_address=0;
    wire [7:0] data,event_data;
    wire [31:0] event_bytes,generation;
    wire [511:0] status;
    wire ready,passed,failed;
    reg [7:0] history[0:99999];
    integer c,i,checks=0,events,expected_result;
    reg [47:0] first_tick,a,b,decision,anchor,observed;
    reg [127:0] event_word;
    integer valid_events,result_events,b_events;
    rule_bringup_selftest #(.ADDR_BITS(10),.NORMAL_DELAY(100),.EARLY_DELAY(20),
        .LATE_DELAY(220),.EARLY_MIN(50),.MAX_DELAY(200),.BIT_PERIOD(10)) dut(
        .clk(clk),.rst_n(rst_n),.diagnostic_start(start),.diagnostic_case(scenario),
        .diagnostic_address(address),.diagnostic_data(data),.event_byte_address(event_address),
        .event_byte_data(event_data),.event_byte_count(event_bytes),.generation(generation),
        .diagnostic_status(status),.diagnostic_ready(ready),.passed(passed),.failed(failed));
    always @(posedge clk) begin
        if(rst_n && dut.tick<100000) history[dut.tick]=dut.levels;
        if(rst_n && (dut.accepted || dut.rising[1] || dut.result_valid))
            $display("RULE_TRACE tick%0d ready%0b busy%0b accept%0b rise%0b result%0d min%0d max%0d enable%0b cancel%0b",dut.tick,dut.ready,dut.busy,dut.accepted,dut.rising[1],dut.result,dut.min_delay,dut.rule0.max_ticks,dut.rule0.enable,dut.rule0.cancel);
    end
    initial begin
        repeat(3) @(negedge clk);rst_n=1;
        for(c=0;c<4;c=c+1) begin
            wait(ready);#1;
            if(!passed || failed || status[7:0]!=2 || status[15:8]!=1 || generation!=c+1)
                $fatal(1,"Internal case failed %0d status%h",c,status);
            expected_result=c==0 ? 1 : c==1 ? 2 : 3;
            if(status[31:24]!=expected_result || status[95:64]!=1024 || status[127:112]*16!=event_bytes)
                $fatal(1,"Header mismatch case%0d",c);
            first_tick=status[175:128];anchor=status[223:176];decision=status[271:224];
            a=status[319:272];b=status[367:320];observed=status[415:368];
            if(status[447:416]!=716 || anchor-decision>=5 || anchor-first_tick!=716*5)
                $fatal(1,"Anchor geometry case%0d",c);
            if(c==0 && b-a!=100 || c==1 && b-a!=20 || c>=2 && (b!=0 || decision-a!=201))
                $fatal(1,"Rule timing case%0d delta%0d decision%0d",c,b-a,decision-a);
            if(c==2 && observed-a!=220 || c==3 && observed!=0) $fatal(1,"Late/missing scope");
            for(i=0;i<1024;i=i+1) begin
                @(negedge clk);address=i;
                @(posedge clk);#1;
                if(data!==history[first_tick+i*5]) $fatal(1,"Sample history case%0d offset%0d",c,i);
                checks=checks+1;
            end
            valid_events=0;result_events=0;b_events=0;events=event_bytes/16;
            for(i=0;i<event_bytes;i=i+1) begin
                @(negedge clk);event_address=i;
                @(posedge clk);#1;event_word[(i%16)*8 +:8]=event_data;
                if(i%16==15) begin
                    if(event_word[127:112]!=0 || event_word[97:86]!=0 || event_word[101:99]!=0 || event_word[105:103]!=0)
                        $fatal(1,"Reserved event bits");
                    if(event_word[72]) begin
                        valid_events=valid_events+1;
                        if(event_word[47:0]!=a || event_word[80:73]!=8'ha5 || !event_word[102]) $fatal(1,"UART event identity");
                    end
                    if(event_word[85:82]!=0) begin
                        result_events=result_events+1;
                        if(event_word[85:82]!=expected_result || event_word[47:0]!=decision) $fatal(1,"Result event identity");
                    end
                    if(event_word[57]) b_events=b_events+1;
                end
            end
            if(valid_events!=1 || result_events!=1 || b_events!=(c==3 ? 0 : 1)) $fatal(1,"Event coverage");
            if(c!=3) begin
                @(negedge clk);scenario=c+1;start=1;
                @(negedge clk);start=0;
            end
        end
        $display("RULE_BRINGUP_TB_PASS CASES=4 SAMPLE_CHECKS=%0d",checks);$finish;
    end
    initial begin #5000000;$fatal(1,"Integration watchdog");end
endmodule
