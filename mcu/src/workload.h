#ifndef WORKLOAD_H
#define WORKLOAD_H
#include <stdint.h>
#define WL_SAMPLES 64u
#define WL_PASSES 3u
#define WL_IRQ_ROUNDS 32u
#define WL_MAGIC 0x574c3031u
uint32_t workload_compute(const uint16_t *input);
uint32_t workload_irq_step(uint32_t state);
uint32_t workload_profile(uint32_t id);
uint32_t workload_period(uint32_t profile);
void workload_input(uint16_t *input);
void workload_marker(uint32_t id, uint32_t profile, uint32_t phase, uint8_t *packet);
#endif
