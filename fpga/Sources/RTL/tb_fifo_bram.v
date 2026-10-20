`timescale 1ns/1ps
module fifo_bram_worker #(parameter integer DEPTH=2048,parameter integer SEED=1)(output reg done=0);
    reg clk=0;always #10 clk=~clk;
    reg rst_n=1,push_valid=0,pop_ready=0;reg [7:0] push_data=0;
    wire ready_d,ready_r,valid_d,valid_r;wire [7:0] data_d,data_r;
    byte_fifo_bram #(.DEPTH(DEPTH)) dut(.clk(clk),.rst_n(rst_n),.push_valid(push_valid),.push_data(push_data),.push_ready(ready_d),.pop_valid(valid_d),.pop_data(data_d),.pop_ready(pop_ready));
    byte_fifo #(.DEPTH(DEPTH)) reference(.clk(clk),.rst_n(rst_n),.push_valid(push_valid),.push_data(push_data),.push_ready(ready_r),.pop_valid(valid_r),.pop_data(data_r),.pop_ready(pop_ready));
    reg [7:0] model[0:DEPTH-1];integer used=0,rd=0,wr=0;
    integer cycles=0,pushes=0,pops=0,byte_checks=0,resets=0,full_pop_push=0;
    integer i,k;reg p,q;reg [31:0] rng=32'h71f0a700 ^ DEPTH ^ (SEED*32'h9e3779b9);
    task check_outputs;
        begin
            if({ready_d,valid_d}!={ready_r,valid_r} || valid_d!==(used!=0) ||
                ready_d!==((used<DEPTH) || (used!=0 && pop_ready)))
                $fatal(1,"FIFO_HANDSHAKE depth=%0d seed=%0d cycle=%0d used=%0d ready=%b/%b valid=%b/%b",DEPTH,SEED,cycles,used,ready_d,ready_r,valid_d,valid_r);
            if(used!=0) begin
                if(data_d!==data_r || data_d!==model[rd])
                    $fatal(1,"FIFO_BYTE depth=%0d seed=%0d cycle=%0d used=%0d rd=%0d want=%02x old=%02x new=%02x",DEPTH,SEED,cycles,used,rd,model[rd],data_r,data_d);
                byte_checks=byte_checks+1;
            end
        end
    endtask
    always @(posedge clk or negedge rst_n) begin
        if(!rst_n) begin used=0;rd=0;wr=0;resets=resets+1;end
        else begin
            check_outputs();cycles=cycles+1;
            p=push_valid && ready_d;q=valid_d && pop_ready;
            if(used==DEPTH && p && q) full_pop_push=full_pop_push+1;
            if(q) begin rd=rd==DEPTH-1 ? 0 : rd+1;pops=pops+1;end
            if(p) begin model[wr]=push_data;wr=wr==DEPTH-1 ? 0 : wr+1;pushes=pushes+1;end
            used=used+p-q;
        end
        #1;check_outputs();
    end
    task step;
        input pv;input [7:0] value;input pr;
        begin @(negedge clk);push_valid=pv;push_data=value;pop_ready=pr;#1;check_outputs();end
    endtask
    task reset_long;
        begin @(negedge clk);rst_n=0;repeat(3) @(negedge clk);rst_n=1;push_valid=0;pop_ready=0;end
    endtask
    task reset_between_edges;
        begin @(negedge clk);#2;rst_n=0;#2;rst_n=1;#1;check_outputs();end
    endtask
    initial begin
        #2;rst_n=0;repeat(3) @(negedge clk);rst_n=1;
        step(0,0,1);step(1,8'ha5,1);step(1,8'h3c,1);step(0,0,1);step(0,0,1);
        // Exact full capacity; blocked push; full simultaneous replacement and
        // continuous drains cross the address wrap many times.
        for(k=0;k<4;k=k+1) begin
            reset_long();
            for(i=0;i<DEPTH;i=i+1) step(1,(i*73)^(i>>8)^k,0);
            step(1,8'hee,0);
            for(i=0;i<DEPTH*3;i=i+1) step(1,(i*19)^(i>>8)^k,1);
            for(i=0;i<DEPTH+2;i=i+1) step(0,0,1);
        end
        // Reset with pending writes/queued bytes, including a pulse that never
        // crosses a clock edge. Old RAM contents must not become valid data.
        for(k=0;k<20;k=k+1) begin
            step(1,k,0);step(1,k+1,0);reset_between_edges();
            step(1,k+8,0);step(1,k+9,0);step(0,0,1);step(0,0,1);step(0,0,1);
        end
        reset_long();
        for(i=0;i<DEPTH/2;i=i+1) step(1,i^(i>>8),0);
        for(i=0;i<100000;i=i+1) begin
            rng=rng*32'd1664525+32'd1013904223;
            step(rng[31],rng[15:8],rng[19]);
            if(i%4999==217) reset_between_edges();
        end
        // Sustained downstream stall with an attempted producer overflow.
        for(i=0;i<DEPTH+100;i=i+1) step(1,i*37,0);
        for(i=0;i<500;i=i+1) step(1,i,0);
        for(i=0;i<DEPTH+2;i=i+1) step(0,0,1);
        step(0,0,0);@(negedge clk);check_outputs();
        if(pushes<10000 || pops<10000 || byte_checks<10000 || full_pop_push<DEPTH*12)
            $fatal(1,"FIFO_CHECK_COVERAGE depth=%0d pushes=%0d pops=%0d bytes=%0d full_replace=%0d",DEPTH,pushes,pops,byte_checks,full_pop_push);
        $display("FIFO_BRAM_WORKER_PASS depth=%0d seed=%0d cycles=%0d pushes=%0d pops=%0d bytes=%0d resets=%0d full_replace=%0d",DEPTH,SEED,cycles,pushes,pops,byte_checks,resets,full_pop_push);
        done=1;
    end
    initial begin #15000000;$fatal(1,"FIFO_BRAM_WORKER_TIMEOUT depth=%0d",DEPTH);end
endmodule

module tb_fifo_bram;
    wire [6:0] done;
    fifo_bram_worker #(.DEPTH(1)) w1(.done(done[0]));
    fifo_bram_worker #(.DEPTH(2)) w2(.done(done[1]));
    fifo_bram_worker #(.DEPTH(3)) w3(.done(done[2]));
    fifo_bram_worker #(.DEPTH(17)) w17(.done(done[3]));
    fifo_bram_worker #(.DEPTH(256)) w256(.done(done[4]));
    fifo_bram_worker #(.DEPTH(2048)) w2048(.done(done[5]));
    fifo_bram_worker #(.DEPTH(2048),.SEED(2)) w2048b(.done(done[6]));
    initial begin wait(&done);$display("FIFO_BRAM_CONTRACT_PASS depths=1,2,3,17,256,2048 workers=7 random_each=100000");$finish;end
    initial begin #16000000;$fatal(1,"FIFO_BRAM_GLOBAL_TIMEOUT");end
endmodule
