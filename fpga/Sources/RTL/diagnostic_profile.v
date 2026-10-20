`timescale 1ns/1ps
// Human feedback applies only to documented GPIO/SPI profiles, not arbitrary rules.
module diagnostic_profile(input wire [799:0] config_bytes, output wire qualified);
    wire common = config_bytes[16] && config_bytes[18] && config_bytes[10:8]==0 &&
        config_bytes[31:24]==1 && config_bytes[95:80]==16'h011a &&
        config_bytes[160+:16]==16'h0001 && config_bytes[176+:16]==16'hffa5 &&
        config_bytes[200+:24]==0 && config_bytes[480+:16]==0 &&
        config_bytes[496+:16]==16'hffa5 && config_bytes[520+:24]==24'hffc301;
    wire original_demo = config_bytes[224+:48]==25000 &&
        config_bytes[272+:48]==100000 && config_bytes[544+:48]==0 &&
        config_bytes[592+:48]==150000;
    wire workload = config_bytes[23:16]==5 && config_bytes[192+:8]==2 &&
        config_bytes[512+:8]==2 && config_bytes[224+:48]==0 &&
        config_bytes[544+:48]==0 && config_bytes[272+:48]==config_bytes[592+:48] &&
        (config_bytes[272+:48]==175000 || config_bytes[272+:48]==400000);
    assign qualified = common && (original_demo || workload);
endmodule
