`timescale 1ns/1ps
module tb_mvp1_capture;
    reg clk=0;always #5 clk=~clk;
    reg rst_n=0;reg [7:0] raw=1;wire ready;reg valid=0;reg [7:0] cmd=0;
    reg [15:0] length=0;reg [271:0] args=0;
    wire done;wire [15:0] status;wire [10:0] body_length;
    wire [1055:0] body;wire stream;reg [10:0] index=0;wire [7:0] data;
    wire [2:0] state;wire [31:0] session,config_id;
    integer checks=0,timeout_counter,i,j,n;reg [271:0] cfg;reg [1055:0] frozen_meta;
    reg [47:0] a_tick;reg [31:0] expected_session;
    reg [7:0] history[0:131071];integer sample_checks=0;
    always @(posedge clk) if(rst_n) history[dut.tick[16:0]]<=dut.levels;
    mvp1_capture #(.CLOCK_HZ(1382400),.ADDR_BITS(8)) dut(.clk(clk),.rst_n(rst_n),
        .raw_inputs(raw),.target_ready(ready),.command_valid(valid),.command(cmd),.argument_length(length),
        .arguments(args),.command_done(done),.command_status(status),.body_length(body_length),
        .body_snapshot(body),.stream_response(stream),.stream_index(index),.stream_data(data),
        .state(state),.session_id(session),.config_id(config_id));
    task check;
        input condition;input [255:0] label;
        begin if(!condition) $fatal(1,"MVP1_CAPTURE_FAIL %0s state=%0d",label,state);checks=checks+1;end
    endtask
    task request;
        input [7:0] command;input [15:0] len;input [271:0] arguments;input [15:0] expected;
        begin
            @(negedge clk);cmd=command;length=len;args=arguments;valid=1;
            @(negedge clk);valid=0;timeout_counter=0;
            while(!done) begin @(negedge clk);timeout_counter=timeout_counter+1;if(timeout_counter>100) $fatal(1,"backend hung");end
            check(status==expected,"response status");
        end
    endtask
    task commit;
        begin request(8'h10,34,cfg,0);request(8'h11,0,0,0);request(8'h12,0,0,0);
            check(body[303:32]==cfg && body[31:0]==config_id,"atomic config readback");end
    endtask
    task arm_capture;
        begin
            expected_session=session+1;request(8'h20,4,config_id,0);
            check(body[31:0]==expected_session,"ARM session");
            timeout_counter=0;
            while(!ready) begin @(negedge clk);timeout_counter=timeout_counter+1;if(timeout_counter>10000) $fatal(1,"no READY");end
            repeat(20) @(negedge clk);
        end
    endtask
    task send_byte;
        input [7:0] value;integer bitno;
        begin
            raw[cfg[10:8]]=0;repeat(12) @(negedge clk);
            for(bitno=0;bitno<8;bitno=bitno+1) begin raw[cfg[10:8]]=value[bitno];repeat(12) @(negedge clk);end
            raw[cfg[10:8]]=1;repeat(12) @(negedge clk);
        end
    endtask
    task wait_freeze;
        begin
            timeout_counter=0;
            while(state!=4) begin @(negedge clk);timeout_counter=timeout_counter+1;if(timeout_counter>10000) $fatal(1,"no freeze");end
            request(8'h22,4,session,0);frozen_meta=body;
            check(body_length==132 && body[784+:272]==cfg,"frozen config/META size");
            check(body[112+:32]==256,"full sample count");
            request(8'h31,10,{192'd0,16'd256,32'd0,session},0);
            for(i=0;i<256;i=i+1) begin
                index=i;repeat(2) @(negedge clk);
                check(data==history[(frozen_meta[160+:48]+i*cfg[64+:16])%131072],"chronological sample/tick");
                sample_checks=sample_checks+1;
            end
        end
    endtask
    task release_record;
        begin request(8'h32,4,session+1,6);request(8'h32,4,session,0);check(state==0,"explicit release");end
    endtask
    initial begin
        repeat(5) @(negedge clk);rst_n=1;repeat(5) @(negedge clk);
        request(2,0,0,0);check(body_length==34 && body[7:0]==1 && body[79:72]==8,"INFO profile/channels");
        request(8'h11,0,0,4);request(8'h20,4,999,14);request(8'h22,4,0,8);
        cfg={48'd500,48'd200,8'd0,8'd2,8'hff,8'ha5,32'd77,32'd178,16'd5,32'd115200,8'd0,8'd1,8'd0,8'd1};
        request(8'h10,33,cfg,3);args=cfg;args[15:8]=8;request(8'h10,34,args,4);
        args=cfg;args[175:168]=1;request(8'h10,34,args,4);
        args=cfg;args[151:144]=8'ha5;args[159:152]=8'h0f;request(8'h10,34,args,4);
        commit();check(dut.period==12,"sequential baud division");
        for(n=0;n<4;n=n+1) begin
            raw=1;arm_capture();request(8'h10,34,cfg,5);request(8'h20,4,config_id,5);
            request(8'h32,4,session,8);send_byte(8'hd0+n);repeat(24) @(negedge clk);send_byte(8'ha5);
            a_tick=dut.selected_a;check(dut.have_a,"accepted UART A5");
            if(n!=3) begin
                j=(n==0 ? 300 : n==1 ? 100 : 550);
                while(dut.tick-a_tick<j) @(negedge clk);
                raw[1]=1;repeat(20) @(negedge clk);raw[1]=0;
            end
            wait_freeze();check(frozen_meta[528+:8]==(n==0 ? 1 : n==1 ? 2 : 3),"four rule outcomes");
            check(frozen_meta[584+:32]==1,"accepted once");
            if(n==2) check(frozen_meta[11] && frozen_meta[480+:48]-a_tick>500,"late response saved");
            if(n==3) check(!frozen_meta[11] && frozen_meta[480+:48]==0,"no B only within window");
            request(8'h22,4,session+1,6);request(8'h11,0,0,5);
            request(8'h31,10,{192'd0,16'd10,32'd256,session},0);check(stream && body_length==10,"sample EOF");
            request(8'h31,10,{192'd0,16'd0,32'd0,session},4);
            request(8'h30,10,{192'd0,16'd16,32'd1,session},4);
            release_record();
        end
        // Mask/non-matching bytes do not invent A; DISARM preserves no-trigger data.
        raw=1;arm_capture();send_byte(8'hb5);request(8'h21,4,session,0);
        request(8'h22,4,session,0);check(body[12] && !body[8] && !body[9] && body[528+:8]==0,"manual no-A freeze");release_record();
        // Live transaction cancellation is not TIMEOUT and is explicitly counted.
        raw=1;arm_capture();send_byte(8'ha5);request(8'h21,4,session,0);request(8'h22,4,session,0);
        check(body[528+:8]==4 && body[712+:32]==1 && body[680+:32]==0,"cancelled DISARM");release_record();
        // Anomaly action keeps PASS running; RECORD never triggers.
        cfg[167:160]=1;commit();raw=1;arm_capture();send_byte(8'ha5);repeat(300) @(negedge clk);
        raw[1]=1;repeat(30) @(negedge clk);raw[1]=0;check(state==2 && dut.pass_count==1,"anomaly PASS continues");
        request(8'h21,4,session,0);release_record();
        cfg[167:160]=0;commit();raw=1;arm_capture();send_byte(8'ha5);repeat(550) @(negedge clk);
        check(state==2 && dut.timeout_count==1,"record TIMEOUT continues");request(8'h21,4,session,0);release_record();
        // Dynamic CH routing, falling edge, masked matching, all divisor options.
        cfg[167:160]=2;cfg[15:8]=2;cfg[23:16]=3;cfg[31:24]=1;cfg[151:144]=8'ha0;cfg[159:152]=8'hf0;
        for(n=0;n<4;n=n+1) begin
            cfg[64+:16]=(n==0 ? 1 : n==1 ? 5 : n==2 ? 10 : 50);commit();raw=8'h0c;
            arm_capture();send_byte(8'ha5);repeat(300) @(negedge clk);raw[3]=0;
            wait_freeze();check(frozen_meta[528+:8]==1,"routed masked falling PASS");release_record();
        end
        $display("MVP1_CAPTURE_TB_PASS checks=%0d samples=%0d",checks,sample_checks);$finish;
    end
    initial begin #30000000;$fatal(1,"MVP1_CAPTURE_GLOBAL_TIMEOUT");end
endmodule
