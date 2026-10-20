`timescale 1ns/1ps
module mvp5_top(
    input wire sys_clk,input wire sys_rst_n,input wire uart_rxd,output wire uart_txd,
    output wire [3:0] led,input wire [7:0] probe_inputs,output wire target_ready,
    output wire beep,input wire mute_key_n,input wire start_key_n,input wire release_key_n
);
    (* ASYNC_REG="TRUE" *) reg [1:0] reset_release;
    always @(posedge sys_clk or negedge sys_rst_n)
        if(!sys_rst_n) reset_release<=0;else reset_release<={reset_release[0],1'b1};
    wire rst_n=reset_release[1];
    wire [7:0] rx_data,queue_data,tx_data,command,stream_data;
    wire rx_valid,frame_error,queue_ready,queue_valid,consume,tx_valid,tx_ready;
    wire synced,protocol_error,command_valid,command_done,stream_response;
    wire [15:0] argument_length,command_status;wire [10:0] body_length,stream_index;
    wire [799:0] arguments;wire [3295:0] snapshot;
    wire [10:0] body_byte_index;wire [7:0] body_byte;
    wire link_consume,link_idle,local_busy,local_valid;
    wire [7:0] local_command;wire [15:0] local_length;wire [799:0] local_arguments;
    wire [2:0] capture_state;wire [31:0] session_id,config_id;
    wire panel_arm,panel_profile_ok,panel_have_a,panel_gpio_response;
    wire [1:0] panel_have_result;
    wire [3:0] panel_gpio_result,panel_spi_result;
    wire panel_response,panel_good,panel_bad;
    reg [24:0] heartbeat_count;reg heartbeat,error_latched;
    uart_rx rx(.clk(sys_clk),.rst_n(rst_n),.serial_in(uart_rxd),.data(rx_data),.valid(rx_valid),.framing_error(frame_error));
    byte_fifo_bram #(.DEPTH(2048)) queue(.clk(sys_clk),.rst_n(rst_n),.push_valid(rx_valid),
        .push_data(rx_data),.push_ready(queue_ready),.pop_valid(queue_valid),.pop_data(queue_data),.pop_ready(consume));
    assign consume=link_consume && !local_busy;
    mvp5_protocol #(.BACKEND_BYTE_SELECT(1)) link(.clk(sys_clk),.rst_n(rst_n),.rx_data(queue_data),.rx_valid(queue_valid && !local_busy),.rx_ready(link_consume),
        .tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready),.synced(synced),.protocol_error(protocol_error),
        .command_valid(command_valid),.command(command),.argument_length(argument_length),.arguments(arguments),
        .local_control_idle(link_idle),.command_done(command_done && !local_busy),.command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .body_byte_index(body_byte_index),.body_byte(body_byte),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data));
    mvp5_capture capture(.clk(sys_clk),.rst_n(rst_n),.raw_inputs(probe_inputs),.target_ready(target_ready),
        .command_valid(local_busy ? local_valid : command_valid),.command(local_busy ? local_command : command),
        .argument_length(local_busy ? local_length : argument_length),.arguments(local_busy ? local_arguments : arguments),
        .command_done(command_done),.command_status(command_status),.body_length(body_length),.body_snapshot(snapshot),
        .body_byte_index(body_byte_index),.body_byte(body_byte),
        .stream_response(stream_response),.stream_index(stream_index),.stream_data(stream_data),
        .state(capture_state),.session_id(session_id),.config_id(config_id),
        .panel_arm(panel_arm),.panel_profile_ok(panel_profile_ok),.panel_have_a(panel_have_a),
        .panel_gpio_response(panel_gpio_response),.panel_have_result(panel_have_result),
        .panel_gpio_result(panel_gpio_result),.panel_spi_result(panel_spi_result));
    button_demo_control controls(.clk(sys_clk),.rst_n(rst_n),.start_key_n(start_key_n),.release_key_n(release_key_n),
        .link_idle(link_idle && tx_ready && !queue_valid && !rx_valid && !command_valid),
        .capture_state(capture_state),.session_id(session_id),.config_id(config_id),
        .command_done(command_done && local_busy),.command_status(command_status),.busy(local_busy),
        .command_valid(local_valid),.command(local_command),.argument_length(local_length),.arguments(local_arguments));
    diagnostic_panel panel(.clk(sys_clk),.rst_n(rst_n),.arm(panel_arm),.state(capture_state),
        .profile_ok(panel_profile_ok),.have_a(panel_have_a),.have_result(panel_have_result),
        .gpio_response(panel_gpio_response),.gpio_result(panel_gpio_result),.spi_result(panel_spi_result),
        .mute_key_n(mute_key_n),.response_led(panel_response),.good_led(panel_good),
        .bad_led(panel_bad),.beep(beep));
    uart_tx tx(.clk(sys_clk),.rst_n(rst_n),.data(tx_data),.valid(tx_valid),.ready(tx_ready),.serial_out(uart_txd));
    // V2.1 LEDs and the onboard active buzzer are active HIGH.
    assign led={panel_bad,panel_good,panel_response,heartbeat};
    always @(posedge sys_clk) begin
        if(!rst_n) begin heartbeat_count<=0;heartbeat<=0;error_latched<=0;end
        else begin
            if(heartbeat_count==24999999) begin heartbeat_count<=0;heartbeat<=~heartbeat;end
            else heartbeat_count<=heartbeat_count+1'b1;
            if(frame_error || protocol_error || (rx_valid && !queue_ready)) error_latched<=1;
        end
    end
endmodule
