`timescale 1ns/1ps
// Same command backend as host. No auto-repeat/release or host-record discard.
module button_demo_control #(
    parameter integer DEBOUNCE_CYCLES=1000000,
    parameter integer RELEASE_CYCLES=100000000
)(
    input wire clk,input wire rst_n,input wire start_key_n,input wire release_key_n,
    input wire link_idle,input wire [2:0] capture_state,
    input wire [31:0] session_id,input wire [31:0] config_id,
    input wire command_done,input wire [15:0] command_status,
    output wire busy,output reg command_valid,output reg [7:0] command,
    output reg [15:0] argument_length,output reg [799:0] arguments
);
    // Exact Config.to_bytes() (100 bytes), split into 20-byte fields to prevent
    // an over-wide literal silently truncating/shifting the rule layout.
    localparam [799:0] DEMO_CONFIG={
        160'h0000000249f0000000000000ff3c0100ffa50000,
        160'h0000000249f0000000000000ffc30100ffa50000,
        160'h0000000061a800000000000000000000ffa50001,
        160'h0000000186a00000000061a800000002ffa50001,
        160'h0000800000007fff011a00050001c200010f0005};
    localparam [3:0] IDLE=0,WRITE=1,WAIT_WRITE=2,COMMIT=3,WAIT_COMMIT=4,
                     ARM=5,WAIT_ARM=6,RELEASE=7,WAIT_RELEASE=8;
    (* ASYNC_REG="TRUE" *) reg [1:0] start_sync,release_sync;
    reg [31:0] start_count,release_count;
    reg start_seen,release_seen;
    reg [3:0] phase;
    reg owns_record;reg [31:0] owned_session;
    wire start_press=!start_sync[1] && !start_seen && start_count==DEBOUNCE_CYCLES-1;
    wire release_press=!release_sync[1] && !release_seen && release_count==RELEASE_CYCLES-1;
    assign busy=phase!=IDLE;
    always @(posedge clk) begin
        if(!rst_n) begin
            start_sync<=3;release_sync<=3;start_count<=0;release_count<=0;
            start_seen<=0;release_seen<=0;phase<=IDLE;owns_record<=0;owned_session<=0;
            command_valid<=0;command<=0;argument_length<=0;arguments<=0;
        end else begin
            start_sync<={start_sync[0],start_key_n};release_sync<={release_sync[0],release_key_n};
            command_valid<=0;
            if(start_sync[1]) begin start_count<=0;start_seen<=0;end
            else if(!start_seen) begin
                if(start_press) start_seen<=1;else start_count<=start_count+1'b1;
            end
            if(release_sync[1]) begin release_count<=0;release_seen<=0;end
            else if(!release_seen) begin
                if(release_press) release_seen<=1;else release_count<=release_count+1'b1;
            end
            if(capture_state==0 && phase==IDLE) owns_record<=0;
            case(phase)
                IDLE:if(link_idle) begin
                    // Simultaneous presses never discard evidence.
                    if(start_press && release_sync[1] && capture_state==0) phase<=WRITE;
                    else if(release_press && start_sync[1] && capture_state==4 &&
                            owns_record && session_id==owned_session) phase<=RELEASE;
                end
                WRITE:begin
                    command<=8'h10;argument_length<=100;arguments<=DEMO_CONFIG;
                    command_valid<=1;phase<=WAIT_WRITE;
                end
                WAIT_WRITE:if(command_done) phase<=command_status==0 ? COMMIT : IDLE;
                COMMIT:begin
                    command<=8'h11;argument_length<=0;arguments<=0;
                    command_valid<=1;phase<=WAIT_COMMIT;
                end
                WAIT_COMMIT:if(command_done) phase<=command_status==0 ? ARM : IDLE;
                ARM:begin
                    command<=8'h20;argument_length<=4;arguments<={768'd0,config_id};
                    command_valid<=1;phase<=WAIT_ARM;
                end
                WAIT_ARM:if(command_done) begin
                    if(command_status==0) begin owns_record<=1;owned_session<=session_id;end
                    phase<=IDLE;
                end
                RELEASE:begin
                    command<=8'h32;argument_length<=4;arguments<={768'd0,owned_session};
                    command_valid<=1;phase<=WAIT_RELEASE;
                end
                WAIT_RELEASE:if(command_done) begin
                    if(command_status==0) owns_record<=0;
                    phase<=IDLE;
                end
                default:phase<=IDLE;
            endcase
        end
    end
endmodule
