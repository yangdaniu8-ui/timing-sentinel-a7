`timescale 1ns/1ps
module tb_external_rule_capture;
    reg clk=0;always #10 clk=~clk;
    reg rst_n=0,start=0,stop=0,serial=1,gpio=0;
    reg [7:0] addr=0;
    reg [13:0] event_addr=0;
    wire [7:0] sample,event_byte;
    wire [31:0] event_bytes,generation;
    wire [511:0] status;
    wire ready,available,frozen,failed;
    external_rule_capture #(.ADDR_BITS(8),.BIT_PERIOD(16),.MIN_DELAY(30),
        .MAX_DELAY(100),.WATCHDOG_CYCLES(6000)) dut(
        .clk(clk),.rst_n(rst_n),.diagnostic_start(start),.diagnostic_stop(stop),
        .target_uart(serial),.target_gpio(gpio),.target_ready(ready),
        .diagnostic_address(addr),.diagnostic_data(sample),
        .event_byte_address(event_addr),.event_byte_data(event_byte),
        .event_byte_count(event_bytes),.generation(generation),.diagnostic_status(status),
        .diagnostic_ready(available),.frozen(frozen),.failed(failed));
    reg [7:0] history[0:65535];
    reg [7:0] ref_meta=1,ref_levels=1;
    integer checked=0,scenes=0,i,j,c;
    reg [47:0] first,a,b,decision,observed;
    reg [7:0] flags;
    reg [127:0] event_word;
    integer uarts,results,edges;
    always @(posedge clk) begin
        if(!rst_n) begin ref_meta<=1;ref_levels<=1;end
        else begin
            history[dut.tick[15:0]]=ref_levels;
            ref_meta<={6'd0,gpio,serial};ref_levels<=ref_meta;
        end
    end
    task cycles(input integer n);begin repeat(n) @(negedge clk);end endtask
    task run_arm;begin
        if(!available) $fatal(1,"not ready for diagnostic ARM");
        start=1;cycles(1);start=0;
        wait(ready);cycles(32); // idle qualifying high, before UART start
    end endtask
    task send_byte(input [7:0] value);integer bitno;begin
        serial=0;cycles(16);
        for(bitno=0;bitno<8;bitno=bitno+1) begin serial=value[bitno];cycles(16);end
        serial=1;cycles(16); // Actual raw TC reference, NOT stop center
    end endtask
    task wait_frozen;begin wait(frozen);cycles(2);if(failed) $fatal(1,"hardware error");end endtask
    task verify_samples;integer n;begin
        first=status[175:128];n=status[95:64];
        for(i=0;i<n;i=i+1) begin
            addr=i;cycles(2);
            if(sample!==history[(first+i*5)&65535]) $fatal(1,"sample mismatch %d",i);
            checked=checked+1;
        end
    end endtask
    task verify_case(input integer c,input integer expected);begin
        wait_frozen;
        if(status[7:0]!=3 || status[15:8]!=1 || status[23:16]!=255 || status[31:24]!=expected)
            $fatal(1,"external status mismatch case %d",c);
        if(status[95:64]!=256 || status[463:456]!=1 || status[471:464]!=0 || status[479:472]!=0 || status[511:480]!=0)
            $fatal(1,"capture/counter mismatch");
        a=status[319:272];b=status[367:320];decision=status[271:224];observed=status[415:368];
        flags=status[455:448];
        if(c<2) begin
            if(b!=decision || (c==0 && (b-a<30 || b-a>100)) || (c==1 && b-a>=30))
                $fatal(1,"matched B mismatch");
        end else if(b!=0 || decision-a!=101) $fatal(1,"timeout not max+1");
        if(c==2 && (observed-a<=100 || !flags[3])) $fatal(1,"late B not retained");
        if(c==3 && flags[3]) $fatal(1,"fabricated missing B");
        verify_samples;
        uarts=0;results=0;edges=0;
        for(i=0;i<event_bytes;i=i+16) begin
            event_word=0;
            for(j=0;j<16;j=j+1) begin event_addr=i+j;cycles(2);event_word[j*8+:8]=event_byte;end
            if(event_word[72]) begin
                uarts=uarts+1;
                if(event_word[80:73]==8'ha5 && (event_word[47:0]!=a || !event_word[102]))
                    $fatal(1,"external UART acceptance event mismatch");
            end
            if(event_word[85:82]!=0) begin
                results=results+1;
                if(event_word[47:0]!=decision || event_word[85:82]!=expected) $fatal(1,"event result mismatch");
            end
            if(event_word[57]) edges=edges+1;
        end
        if(uarts!=2 || results!=1 || edges!=(c==3 ? 0:1)) $fatal(1,"event coverage mismatch");
        scenes=scenes+1;
    end endtask
    initial begin
        cycles(5);rst_n=1;cycles(4);
        if(!available || frozen || ready || generation!=0 || status[15:8]!=0) $fatal(1,"auto ARM or fake frozen at boot");
        for(c=0;c<4;c=c+1) begin
            run_arm;send_byte(8'hd0+c);cycles(40);send_byte(8'ha5);
            if(c!=3) begin cycles(c==0 ? 60:c==1 ? 5:120);gpio=1;cycles(10);gpio=0;end
            verify_case(c,c==0 ? 1:c==1 ? 2:3);
            if(generation!=c+1 || ready) $fatal(1,"generation/READY mismatch");
        end
        run_arm;cycles(17);stop=1;cycles(1);stop=0;wait_frozen;
        if(status[31:24]!=0 || !status[452] || status[448] || status[451] || status[450] || status[449])
            $fatal(1,"manual STOP fabricated A/B/trigger");
        verify_samples;scenes=scenes+1;
        run_arm;wait_frozen;
        if(status[31:24]!=0 || !status[452] || status[449] || ready) $fatal(1,"no-A watchdog fabricated TIMEOUT");
        verify_samples;scenes=scenes+1;
        // Raw A before READY is discarded. Abort a still-prefilling capture.
        start=1;cycles(1);start=0;send_byte(8'ha5);stop=1;cycles(1);stop=0;wait_frozen;
        if(status[463:456]!=0 || status[31:24]!=0) $fatal(1,"pre-READY A accepted");
        verify_samples;scenes=scenes+1;
        if(generation!=7) $fatal(1,"generation accounting mismatch");
        $display("EXTERNAL_RULE_CAPTURE_TB_PASS scenes=%d sample_checks=%d",scenes,checked);$finish;
    end
    initial begin #10000000;$fatal(1,"TB watchdog");end
endmodule
