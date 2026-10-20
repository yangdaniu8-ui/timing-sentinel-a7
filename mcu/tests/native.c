#include <stdio.h>
#include "workload.h"
int main(void) {
    uint16_t input[WL_SAMPLES]; uint8_t b[10]; uint32_t i,j;
    workload_input(input);
    printf("CHECKSUM %08x\n",(unsigned)workload_compute(input));
    printf("IRQ %08x\n",(unsigned)workload_irq_step(0x12345678u));
    for(i=1;i<=4095u;i++) {
        workload_marker(i,workload_profile(i),(i*37u)&255u,b);
        printf("JOB %u %u %u ", (unsigned)i,(unsigned)workload_profile(i),
               (unsigned)workload_period(workload_profile(i)));
        for(j=0;j<10u;j++) printf("%02x",b[j]);
        puts("");
    }
    return 0;
}
