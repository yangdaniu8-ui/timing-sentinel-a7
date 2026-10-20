`timescale 1ns/1ps
// Independent transport-focused regression: all 100 argument lanes, all 412
// snapshot lanes, short-frame clearing, backpressure, stream prefix and cache.
// No hierarchical force, and no substitution for the full top-level oracle.
module tb_selector_byte_backend;
    reg clk=0; always #10 clk=~clk;
    reg rst_n=0,rx_valid=0; reg [7:0] rx_data=0;
    wire rx_ready,tx_valid; wire [7:0] tx_data;
    integer cycles=0;
    wire tx_ready=cycles%13>=4 && cycles%31!=0;
    wire command_valid; wire [7:0] command;
    wire [15:0] argument_length; wire [799:0] arguments;
    reg command_done=0; reg [15:0] command_status=0;
    reg [10:0] body_length=0; reg [3295:0] body_snapshot=0;
    reg stream_response=0; wire [10:0] stream_index;
    wire [7:0] stream_data=(stream_index*17+stream_seed)^8'h96;
    integer wanted_args=0,arg_seed=0,response_seed=0,stream_seed=0;
    integer wanted_body=0; reg wanted_stream=0;
    integer expected_calls=0,actual_calls=0,frames=0,byte_checks=0;
    reg [7:0] expected[0:1035]; integer expected_size=0,got=0;
    reg stalled=0; reg [7:0] stalled_data;
    integer lane;
    localparam [63:0] TOKEN=64'h563412efcdab8967;
    wire [10:0] body_byte_index;
    wire [7:0] reference_byte=body_snapshot>>(body_byte_index*8);
    mvp5_protocol #(.BACKEND_BYTE_SELECT(1)) dut(.clk(clk),.rst_n(rst_n),.rx_data(rx_data),.rx_valid(rx_valid),.rx_ready(rx_ready),
        .tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready),.command_valid(command_valid),
        .command(command),.argument_length(argument_length),.arguments(arguments),.command_done(command_done),
        .command_status(command_status),.body_length(body_length),.body_snapshot(body_snapshot),
        .body_byte_index(body_byte_index),.body_byte(reference_byte),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data));
    function [7:0] pattern;
        input integer n,seed;
        begin pattern=((n*37+seed*19)^(n/7+seed*5)) & 255; end
    endfunction
    function [15:0] crc;
        input [15:0] before; input [7:0] value;
        integer k; reg [15:0] c;
        begin
            c=before;
            for(k=7;k>=0;k=k-1) begin
                if(c[15]^value[k]) c=(c<<1)^16'h1021; else c=c<<1;
            end
            crc=c;
        end
    endfunction
    always @(posedge clk) begin
        cycles<=cycles+1; command_done<=0;
        if(rst_n && command_valid) begin
            actual_calls=actual_calls+1;
            if(command!==8'h66 || argument_length!==wanted_args)
                $fatal(1,"selector backend command/length mismatch");
            for(lane=0;lane<100;lane=lane+1)
                if(arguments[lane*8+:8] !== (lane<wanted_args ? pattern(lane,arg_seed) : 8'd0))
                    $fatal(1,"argument lane mismatch lane=%0d seed=%0d got=%02x",lane,arg_seed,arguments[lane*8+:8]);
            for(lane=0;lane<412;lane=lane+1) body_snapshot[lane*8+:8]<=pattern(lane,response_seed);
            body_length<=wanted_body; stream_response<=wanted_stream;
            command_status<=0; command_done<=1;
        end
        if(rst_n && stalled && (!tx_valid || tx_data!==stalled_data))
            $fatal(1,"TX byte changed under backpressure");
        stalled<=rst_n && tx_valid && !tx_ready;
        stalled_data<=tx_data;
        if(rst_n && tx_valid && tx_ready) begin
            if(got>=expected_size || tx_data!==expected[got])
                $fatal(1,"selector response byte=%0d frame=%0d got=%02x expected=%02x size=%0d",got,frames,tx_data,expected[got],expected_size);
            got=got+1; byte_checks=byte_checks+1;
        end
    end
    task send_byte;
        input [7:0] value; integer waits;
        begin
            @(negedge clk); rx_data=value; rx_valid=1; waits=0;
            while(!rx_ready && waits<50000) begin @(negedge clk); waits=waits+1; end
            if(!rx_ready) $fatal(1,"selector RX timeout");
            @(posedge clk); #1; @(negedge clk); rx_valid=0;
        end
    endtask
    task request;
        input [7:0] cmd; input integer id,args,seed;
        integer i; reg [15:0] c,len; reg [7:0] b;
        begin
            c=16'hffff; len=8+args;
            send_byte(8'h54); send_byte(8'h53);
            for(i=0;i<8;i=i+1) begin
                case(i)
                    0:b=1; 1:b=cmd; 2,3,4,5:b=id>>((i-2)*8);
                    6:b=len[7:0]; 7:b=len[15:8];
                endcase
                c=crc(c,b); send_byte(b);
            end
            for(i=0;i<len;i=i+1) begin
                b=i<8 ? TOKEN>>(i*8) : pattern(i-8,seed);
                c=crc(c,b); send_byte(b);
            end
            send_byte(c[7:0]); send_byte(c[15:8]);
        end
    endtask
    task expected_reply;
        input [7:0] cmd; input integer id,bytes,seed,is_ping,is_stream;
        integer i; reg [15:0] c,len;
        begin
            len=10+bytes; expected_size=12+len; got=0;
            expected[0]=8'h54; expected[1]=8'h53; expected[2]=1; expected[3]=cmd|8'h80;
            for(i=0;i<4;i=i+1) expected[4+i]=id>>(i*8);
            expected[8]=len[7:0]; expected[9]=len[15:8];
            for(i=0;i<8;i=i+1) expected[10+i]=TOKEN>>(i*8);
            expected[18]=0; expected[19]=0;
            for(i=0;i<bytes;i=i+1)
                expected[20+i]=is_ping ? pattern(i,seed) :
                    (is_stream && i>=10 ? ((i-10)*17+stream_seed)^8'h96 : pattern(i,seed));
            c=16'hffff;
            for(i=2;i<expected_size-2;i=i+1) c=crc(c,expected[i]);
            expected[expected_size-2]=c[7:0]; expected[expected_size-1]=c[15:8];
        end
    endtask
    task finish_frame;
        integer waits;
        begin
            waits=0;
            while(got<expected_size && waits<50000) begin @(negedge clk); waits=waits+1; end
            if(got!=expected_size) $fatal(1,"selector reply incomplete");
            repeat(30) @(negedge clk);
            if(actual_calls!=expected_calls) $fatal(1,"selector duplicate backend execution");
            frames=frames+1;
        end
    endtask
    integer n,id;
    initial begin
        repeat(10) @(negedge clk); rst_n=1;
        if(arguments!==800'd0) $fatal(1,"reset did not clear arguments");
        expected_reply(3,0,0,0,0,0); request(3,0,0,0); finish_frame();
        id=1;
        for(n=0;n<6;n=n+1) begin
            wanted_args=100; arg_seed=19+n*31; response_seed=7+n*23;
            wanted_body=412; wanted_stream=0; expected_calls=expected_calls+1;
            expected_reply(8'h66,id,412,response_seed,0,0);
            request(8'h66,id,100,arg_seed); finish_frame();
            // Force no DUT signal. Alter backend input after complete reply;
            // same request must replay the old byte-exact cached frame.
            @(negedge clk); body_snapshot=~body_snapshot;
            expected_reply(8'h66,id,412,response_seed,0,0);
            request(8'h66,id,100,arg_seed); finish_frame(); id=id+1;
        end
        for(n=0;n<6;n=n+1) begin
            wanted_args=n==0 ? 0 : n*17; arg_seed=201-n*13; response_seed=73+n*7;
            case(n) 0:wanted_body=0; 1:wanted_body=1; 2:wanted_body=34;
                3:wanted_body=104; 4:wanted_body=126; 5:wanted_body=411; endcase
            expected_calls=expected_calls+1;
            expected_reply(8'h66,id,wanted_body,response_seed,0,0);
            request(8'h66,id,wanted_args,arg_seed); finish_frame(); id=id+1;
        end
        wanted_args=10; arg_seed=33; response_seed=193; stream_seed=58;
        wanted_body=1014; wanted_stream=1; expected_calls=expected_calls+1;
        expected_reply(8'h66,id,1014,response_seed,0,1);
        request(8'h66,id,wanted_args,arg_seed); finish_frame(); id=id+1;
        expected_reply(1,id,1014,197,1,0); request(1,id,1014,197); finish_frame();
        @(negedge clk); rst_n=0; repeat(4) @(negedge clk);
        if(arguments!==800'd0 || tx_valid) $fatal(1,"reset retains arguments/TX");
        if(frames!=21 || byte_checks!=8110 || actual_calls!=13)
            $fatal(1,"selector contract did not complete every expected case");
        $display("SELECTOR_BYTE_BACKEND_PASS frames=%0d bytes=%0d calls=%0d",frames,byte_checks,actual_calls); $finish;
    end
    initial begin #10000000; $fatal(1,"SELECTOR_GLOBAL_TIMEOUT"); end
endmodule
