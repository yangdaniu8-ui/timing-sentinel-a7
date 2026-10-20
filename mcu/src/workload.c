/* Actual bounded 8-tap fixed-point FIR + rolling output checksum.
 * Synthetic input is identical in every condition; IRQ work never edits it.
 * All signed products/sums stay within int32_t; hash wrap is uint32_t. */
#include "workload.h"
void workload_input(uint16_t *input) {
    uint32_t i, x=0x20261011u;
    for(i=0;i<WL_SAMPLES;i++) {
        x=x*1664525u+1013904223u;
        input[i]=(uint16_t)((x>>16)&1023u);
    }
}
uint32_t workload_compute(const uint16_t *input) {
    static const int32_t coeff[8]={-3,5,11,19,19,11,5,-3};
    uint32_t p,i,k,h=2166136261u;
    for(p=0;p<WL_PASSES;p++) for(i=0;i<WL_SAMPLES;i++) {
        int32_t sum=0;
        for(k=0;k<8u;k++) sum+=((int32_t)input[(i+k)&63u]-512)*coeff[k];
        h=(h^(uint32_t)sum)*16777619u;
        h=(h^(i+64u*p))*16777619u;
    }
    return h;
}
uint32_t workload_irq_step(uint32_t x) {
    uint32_t i;
    for(i=0;i<WL_IRQ_ROUNDS;i++) { x^=x<<13; x^=x>>17; x^=x<<5; }
    return x;
}
uint32_t workload_profile(uint32_t id) {
    static const uint32_t order[6]={0,1,2,2,1,0};
    return order[(id-1u)%6u];
}
uint32_t workload_period(uint32_t profile) {
    return profile==1u ? 1000u : profile==2u ? 125u : 0u;
}
void workload_marker(uint32_t id,uint32_t profile,uint32_t phase,uint8_t *b) {
    uint32_t check=(id^(id>>8)^profile^phase^0x96u)&255u;
    b[0]=0xe2u; b[1]=(uint8_t)(0x40u|((id>>8)&15u));
    b[2]=(uint8_t)(0x40u|((id>>4)&15u)); b[3]=(uint8_t)(0x40u|(id&15u));
    b[4]=(uint8_t)(0x60u|profile); b[5]=(uint8_t)(0x50u|(phase>>4));
    b[6]=(uint8_t)(0x50u|(phase&15u)); b[7]=(uint8_t)(0x70u|(check>>4));
    b[8]=(uint8_t)(0x70u|(check&15u)); b[9]=0xa5u;
}
