`timescale 1ns/1ps
// Independent human-facing observer. No actuator or capture-control outputs.
// All timer durations are nominal; the measurement clock is not recalibrated.
module diagnostic_panel #(
    parameter integer CLOCK_HZ=50000000,
    parameter integer MS_CYCLES=CLOCK_HZ/1000,
    parameter integer MUTE_MS=20,
    parameter integer RESPONSE_MS=500,
    parameter integer GOOD_BEEP_MS=100,
    parameter integer FAULT_SLOT_MS=150
)(
    input wire clk,input wire rst_n,input wire arm,input wire [2:0] state,
    input wire profile_ok,input wire have_a,input wire [1:0] have_result,
    input wire gpio_response,input wire [3:0] gpio_result,input wire [3:0] spi_result,
    input wire mute_key_n,output wire response_led,output reg good_led,
    output reg bad_led,output wire beep
);
    (* ASYNC_REG="TRUE" *) reg [1:0] mute_sync;
    reg [31:0] debounce_cycles,ms_cycles;
    reg muted,muted_press_seen,evaluated;
    reg [2:0] previous_state;
    reg [15:0] response_left,sound_age;
    reg sound_active,sound_fault;
    wire ms_enable=ms_cycles==MS_CYCLES-1;
    wire terminal_known=(gpio_result>=1 && gpio_result<=3) &&
                        (spi_result>=1 && spi_result<=3);
    wire final_good=gpio_result==1 && spi_result==1;
    // V2.1 has an ACTIVE buzzer: DC envelopes, not an audio-frequency PWM.
    wire sound_envelope=sound_fault ?
        (sound_age<FAULT_SLOT_MS ||
         (sound_age>=2*FAULT_SLOT_MS && sound_age<3*FAULT_SLOT_MS) ||
         (sound_age>=4*FAULT_SLOT_MS && sound_age<5*FAULT_SLOT_MS)) :
        sound_age<GOOD_BEEP_MS;
    assign beep=rst_n && sound_active && sound_envelope && !muted;
    assign response_led=response_left!=0;
    always @(posedge clk) begin
        if(!rst_n) begin
            mute_sync<=2'b11;debounce_cycles<=0;muted<=0;muted_press_seen<=0;
            ms_cycles<=0;previous_state<=0;evaluated<=0;response_left<=0;
            good_led<=0;bad_led<=0;sound_age<=0;sound_active<=0;sound_fault<=0;
        end else begin
            mute_sync<={mute_sync[0],mute_key_n};previous_state<=state;
            if(ms_enable) ms_cycles<=0;else ms_cycles<=ms_cycles+1'b1;
            if(mute_sync[1]) begin debounce_cycles<=0;muted_press_seen<=0;end
            else if(!muted_press_seen) begin
                if(debounce_cycles==MUTE_MS*MS_CYCLES-1) begin
                    muted<=1;muted_press_seen<=1;
                end else debounce_cycles<=debounce_cycles+1'b1;
            end
            if(ms_enable) begin
                if(response_left!=0) response_left<=response_left-1'b1;
                if(sound_active) begin
                    if((sound_fault && sound_age>=5*FAULT_SLOT_MS-1) ||
                       (!sound_fault && sound_age>=GOOD_BEEP_MS-1)) sound_active<=0;
                    else sound_age<=sound_age+1'b1;
                end
            end
            if(arm) begin
                evaluated<=0;good_led<=0;bad_led<=0;response_left<=0;
                sound_active<=0;sound_age<=0;muted<=muted_press_seen && !mute_sync[1];
            end else begin
                // Stretch an observed physical PB0 edge for human visibility.
                if(profile_ok && have_a && gpio_response && state>=1 && state<=3)
                    response_left<=RESPONSE_MS;
                // Wait a full frozen clock for CANCEL/INCOMPLETE finalization.
                if(state==4 && previous_state==4 && !evaluated) begin
                    evaluated<=1;
                    if(profile_ok && have_a && have_result==2'b11 && terminal_known) begin
                        good_led<=final_good;bad_led<=!final_good;
                        sound_fault<=!final_good;sound_age<=0;sound_active<=1;
                    end
                    // No A, unsupported config, CANCEL and INCOMPLETE remain
                    // unclassified and silent, never fabricate a DUT fault.
                end
            end
        end
    end
endmodule
