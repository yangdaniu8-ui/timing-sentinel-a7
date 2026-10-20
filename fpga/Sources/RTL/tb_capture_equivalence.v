`timescale 1ns/1ps
// Unchanged USR41 reference vs candidate, only public-port cycle comparison.
module capture_miter #(parameter integer BITS=16)(input wire clk, output reg done=0);
    localparam integer DEPTH=1<<BITS;
    reg rst_n=0,arm=0,stop=0,release_capture=0,trigger=0,read_enable=0;
    reg [7:0] sample_data=0;
    reg [47:0] logic_tick=48'hffffffff0000;
    reg [15:0] divisor=1;
    reg [31:0] pre_count=DEPTH/2-1,post_count=DEPTH/2;
    reg [BITS-1:0] read_address=0;
    wire [223:0] actual,expected;
    wire [2:0] s0,s1;
    wire ready0,ready1,error0,error1,tv0,tv1,ms0,ms1,rv0,rv1;
    wire [31:0] count0,count1,index0,index1;
    wire [47:0] first0,first1,anchor0,anchor1,decision0,decision1;
    wire [7:0] data0,data1;
    capture_buffer #(.ADDR_BITS(BITS)) candidate(
        .clk(clk),.rst_n(rst_n),.sample_data(sample_data),.logic_tick(logic_tick),
        .arm(arm),.stop(stop),.release_capture(release_capture),.sample_divisor(divisor),
        .pre_count(pre_count),.post_count(post_count),.trigger(trigger),.state(s0),
        .ready(ready0),.command_error(error0),.valid_count(count0),.trigger_valid(tv0),
        .manual_stop(ms0),.first_tick(first0),.anchor_tick(anchor0),.decision_tick(decision0),
        .trigger_index(index0),.read_enable(read_enable),.read_address(read_address),
        .read_data(data0),.read_valid(rv0));
    capture_buffer_reference #(.ADDR_BITS(BITS)) reference(
        .clk(clk),.rst_n(rst_n),.sample_data(sample_data),.logic_tick(logic_tick),
        .arm(arm),.stop(stop),.release_capture(release_capture),.sample_divisor(divisor),
        .pre_count(pre_count),.post_count(post_count),.trigger(trigger),.state(s1),
        .ready(ready1),.command_error(error1),.valid_count(count1),.trigger_valid(tv1),
        .manual_stop(ms1),.first_tick(first1),.anchor_tick(anchor1),.decision_tick(decision1),
        .trigger_index(index1),.read_enable(read_enable),.read_address(read_address),
        .read_data(data1),.read_valid(rv1));
    assign actual={s0,ready0,error0,count0,tv0,ms0,first0,anchor0,decision0,index0,rv0,data0};
    assign expected={s1,ready1,error1,count1,tv1,ms1,first1,anchor1,decision1,index1,rv1,data1};
    integer cycles=0,reads=0,cases=0,d,p,i,limit;
    reg [31:0] random_state=32'h14ca701d;
    always @(posedge clk) begin
        logic_tick<=logic_tick+1'b1;
        sample_data<=sample_data+8'h37;
        #1;
        if(actual !== expected) $fatal(1,"CAPTURE_MITER bits=%0d cycle=%0d actual=%h expected=%h",BITS,cycles,actual,expected);
        cycles=cycles+1;
        if(rv0) reads=reads+1;
    end
    task step; begin @(negedge clk); end endtask
    task clear_controls;
        begin arm=0;stop=0;release_capture=0;trigger=0;read_enable=0; end
    endtask
    task reset_pair;
        begin clear_controls();rst_n=0;repeat(3) step();rst_n=1;step(); end
    endtask
    task begin_capture;
        begin arm=1;step();arm=0; end
    endtask
    task wait_ready;
        begin limit=0;while(!ready0 && limit<DEPTH*50+100) begin step();limit=limit+1;end
            if(!ready0) $fatal(1,"CAPTURE_MITER READY timeout bits=%0d",BITS);end
    endtask
    task wait_frozen;
        begin limit=0;while(s0!=4 && limit<DEPTH*50+100) begin step();limit=limit+1;end
            if(s0!=4) $fatal(1,"CAPTURE_MITER freeze timeout bits=%0d",BITS);end
    endtask
    task sweep_reads;
        begin read_enable=1;for(i=0;i<DEPTH;i=i+1) begin read_address=i;step();end
            read_enable=0;step();end
    endtask
    initial begin
        reset_pair();
        if(BITS==16) begin
            // Full65536 records, every divisor, timestamp wrap, ring wrap,
            // every chronological read address, all trigger sampling phases.
            for(d=0;d<4;d=d+1) begin
                divisor=d==0 ? 1 : d==1 ? 5 : d==2 ? 10 : 50;
                pre_count=DEPTH/2-1;post_count=DEPTH/2;
                begin_capture();wait_ready();
                if(d==0) repeat(DEPTH+7) step();
                trigger=1;step();trigger=0;wait_frozen();
                if(count0!=DEPTH || !tv0 || ms0) $fatal(1,"CAPTURE_MITER full depth failed");
                sweep_reads();release_capture=1;step();release_capture=0;cases=cases+1;
                pre_count=1;post_count=DEPTH-2;
                for(p=0;p<divisor;p=p+1) begin
                    begin_capture();wait_ready();repeat(p) step();
                    trigger=1;step();trigger=0;repeat(2) step();
                    stop=1;step();stop=0;wait_frozen();
                    read_enable=1;read_address=0;step();read_address=DEPTH-1;step();
                    read_enable=0;step();release_capture=1;step();release_capture=0;
                    cases=cases+1;
                end
            end
            // Largest legal pre/post, empty manual stop, stop-over-trigger,
            // rejected32-bit high counts and unsupported divisors.
            for(d=0;d<2;d=d+1) begin
                divisor=1;pre_count=d==0?0:DEPTH-2;post_count=DEPTH-1-pre_count;
                begin_capture();wait_ready();trigger=1;stop=1;step();trigger=0;stop=0;
                wait_frozen();read_enable=1;read_address=0;step();read_address=DEPTH-1;step();
                read_enable=0;step();release_capture=1;step();release_capture=0;cases=cases+1;
            end
            pre_count=DEPTH/2-1;post_count=DEPTH/2;divisor=50;
            begin_capture();stop=1;step();stop=0;wait_frozen();
            if(count0!=0 || first0!=0) $fatal(1,"CAPTURE_MITER empty stop failed");
            release_capture=1;step();release_capture=0;cases=cases+1;
            pre_count=32'h80007fff;post_count=32768;divisor=1;begin_capture();step();
            if(s0!=0) $fatal(1,"CAPTURE_MITER invalid high count accepted");
            pre_count=32767;post_count=32'h80008000;begin_capture();step();
            if(s0!=0) $fatal(1,"CAPTURE_MITER invalid high post accepted");
            post_count=32768;divisor=16'h8001;begin_capture();step();
            if(s0!=0) $fatal(1,"CAPTURE_MITER invalid high divisor accepted");cases=cases+3;
        end else begin
            // Ensure random captures really freeze and return valid data.
            for(p=0;p<200;p=p+1) begin
                random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
                case(random_state[3:2]) 0:divisor=1;1:divisor=5;2:divisor=10;3:divisor=50;endcase
                pre_count=random_state[8:4]%(DEPTH-1);post_count=DEPTH-1-pre_count;
                begin_capture();wait_ready();repeat(random_state[15:10]) step();
                trigger=1;step();trigger=0;
                if(random_state[20]) begin repeat(2) step();stop=1;step();stop=0;end
                wait_frozen();sweep_reads();release_capture=1;step();release_capture=0;
            end
            // Deterministic random legal/illegal command competition on16-depth.
            for(p=0;p<10000;p=p+1) begin
                random_state={random_state[30:0],random_state[31]^random_state[21]^random_state[1]^random_state[0]};
                arm=random_state[0];stop=random_state[1];release_capture=random_state[2];
                trigger=random_state[3];read_enable=random_state[4];read_address=random_state[8+:BITS];
                pre_count=random_state[12+:BITS];post_count=DEPTH-1-pre_count;
                if(random_state[20:16]==0) pre_count=32'hfffffff0;
                case(random_state[24:22])
                    0:divisor=1;1:divisor=5;2:divisor=10;3:divisor=50;
                    4:divisor=0;5:divisor=16'h8001;default:divisor=3;
                endcase
                rst_n=(random_state[31:25]!=0);step();
            end
            reset_pair();cases=10200;
            if(reads==0) $fatal(1,"CAPTURE_MITER random valid-read coverage missing");
        end
        clear_controls();repeat(3) step();done=1;
        $display("CAPTURE_MITER_PART_PASS bits=%0d cycles=%0d reads=%0d cases=%0d seed=14ca701d",BITS,cycles,reads,cases);
    end
endmodule
module tb_capture_equivalence;
    reg clk=0;always #10 clk=~clk;
    wire small_done,full_done;
    capture_miter #(.BITS(4)) u_small_depth(.clk(clk),.done(small_done));
    capture_miter #(.BITS(16)) full(.clk(clk),.done(full_done));
    initial begin wait(small_done && full_done);$display("CAPTURE_EQUIVALENCE_PASS full_depth=65536 divisors=4 random_cycles=10000 random_captures=200");$finish;end
    initial begin #200000000;$fatal(1,"CAPTURE_EQUIVALENCE_GLOBAL_TIMEOUT");end
endmodule
